#!/usr/bin/env python3
"""
Telegram Bot for Video Thumbnail Automatic Watermark Replacement
Production-ready version for Render deployment.
"""

import os
import sys
import logging
import tempfile
from typing import Optional, Tuple, Dict, Any, List

# Third-party imports
import numpy as np
from scipy import ndimage
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode
from PIL import Image, ImageDraw, ImageFont

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Environment variables with validation
BOT_TOKEN = os.getenv("BOT_TOKEN")
FONT_PATH = os.getenv("FONT_PATH", None)

# Optional configuration
WATERMARK_DETECTION_THRESHOLD = int(os.getenv("WATERMARK_DETECTION_THRESHOLD", "30"))
WATERMARK_COLOR_DIFFERENCE = int(os.getenv("WATERMARK_COLOR_DIFFERENCE", "50"))

if not BOT_TOKEN:
    logger.error("BOT_TOKEN environment variable is not set!")
    sys.exit(1)

# Constants
MAX_THUMBNAIL_SIZE = 320
DEFAULT_WATERMARK = "My Watermark"


class AutomaticWatermarkDetector:
    """Automatically detects watermark regions in images using multiple techniques."""
    
    def __init__(self, detection_threshold: int = 30, color_difference: int = 50):
        self.detection_threshold = detection_threshold
        self.color_difference = color_difference
    
    def detect_watermark_regions(self, img: Image.Image) -> List[Tuple[int, int, int, int]]:
        """Detect potential watermark regions."""
        regions = []
        
        # Convert to RGB if needed
        if img.mode == 'RGBA':
            img = img.convert('RGB')
        
        img_array = np.array(img)
        
        # Method 1: Corner-based detection (most common)
        corner_regions = self._detect_corner_watermarks(img_array)
        regions.extend(corner_regions)
        
        # Method 2: Text region detection
        text_regions = self._detect_text_regions(img_array)
        regions.extend(text_regions)
        
        # Method 3: Uniform region detection
        uniform_regions = self._detect_uniform_regions(img_array)
        regions.extend(uniform_regions)
        
        # Merge overlapping regions
        merged_regions = self._merge_overlapping_regions(regions)
        
        # Filter by size
        filtered_regions = self._filter_by_size(merged_regions, img.size)
        
        return filtered_regions
    
    def _detect_text_regions(self, img_array: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """Detect text-like regions."""
        regions = []
        
        if len(img_array.shape) == 3:
            gray = np.mean(img_array[:, :, :3], axis=2)
        else:
            gray = img_array
        
        # Edge detection
        gy, gx = np.gradient(gray.astype(float))
        gradient_magnitude = np.sqrt(gx**2 + gy**2)
        
        edge_mask = gradient_magnitude > self.detection_threshold
        
        if edge_mask.any():
            dilated = ndimage.binary_dilation(edge_mask, iterations=2)
            labeled, num_features = ndimage.label(dilated)
            
            for i in range(1, num_features + 1):
                region = np.where(labeled == i)
                if len(region[0]) > 50:
                    y1, y2 = region[0].min(), region[0].max()
                    x1, x2 = region[1].min(), region[1].max()
                    
                    width = x2 - x1
                    height = y2 - y1
                    if height > 0 and width / height > 2:
                        regions.append((x1-5, y1-5, x2+5, y2+5))
        
        return regions
    
    def _detect_uniform_regions(self, img_array: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """Detect uniform colored regions."""
        regions = []
        
        if len(img_array.shape) == 3:
            h, w = img_array.shape[:2]
            
            for scale in [0.1, 0.15]:
                window_h = int(h * scale)
                window_w = int(w * scale)
                
                if window_h < 10 or window_w < 10:
                    continue
                
                for y in range(0, h - window_h, window_h // 2):
                    for x in range(0, w - window_w, window_w // 2):
                        window = img_array[y:y+window_h, x:x+window_w, :3]
                        variance = np.var(window)
                        
                        if variance < 1000:
                            if y > 0 and x > 0:
                                surrounding = img_array[
                                    max(0, y-10):min(h, y+window_h+10),
                                    max(0, x-10):min(w, x+window_w+10),
                                    :3
                                ]
                                surrounding_var = np.var(surrounding)
                                
                                if surrounding_var > variance * 2:
                                    regions.append((x, y, x+window_w, y+window_h))
        
        return regions
    
    def _detect_corner_watermarks(self, img_array: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """Detect corner watermarks."""
        regions = []
        h, w = img_array.shape[:2]
        
        corner_size_h = int(h * 0.25)
        corner_size_w = int(w * 0.25)
        
        corners = [
            (0, 0, corner_size_w, corner_size_h),
            (w - corner_size_w, 0, w, corner_size_h),
            (0, h - corner_size_h, corner_size_w, h),
            (w - corner_size_w, h - corner_size_h, w, h),
        ]
        
        for x1, y1, x2, y2 in corners:
            corner_region = img_array[y1:y2, x1:x2, :3]
            main_region = img_array[corner_size_h:-corner_size_h, corner_size_w:-corner_size_w, :3]
            
            if main_region.size > 0:
                corner_mean = np.mean(corner_region, axis=(0, 1))
                main_mean = np.mean(main_region, axis=(0, 1))
                
                color_diff = np.sum(np.abs(corner_mean - main_mean))
                if color_diff > self.color_difference:
                    gray_corner = np.mean(corner_region, axis=2)
                    gray_main = np.mean(main_region, axis=2)
                    
                    if gray_main.size > 0:
                        corner_mask = np.abs(gray_corner - gray_main.mean()) > 20
                        
                        if corner_mask.any():
                            ys, xs = np.where(corner_mask)
                            exact_y1 = y1 + ys.min()
                            exact_y2 = y1 + ys.max()
                            exact_x1 = x1 + xs.min()
                            exact_x2 = x1 + xs.max()
                            regions.append((exact_x1, exact_y1, exact_x2, exact_y2))
        
        return regions
    
    def _merge_overlapping_regions(self, regions: List[Tuple[int, int, int, int]]) -> List[Tuple[int, int, int, int]]:
        """Merge overlapping regions."""
        if not regions:
            return []
        
        regions = sorted(regions, key=lambda r: (r[2]-r[0])*(r[3]-r[1]), reverse=True)
        merged = []
        
        while regions:
            current = regions.pop(0)
            x1, y1, x2, y2 = current
            
            to_merge = []
            remaining = []
            
            for region in regions:
                rx1, ry1, rx2, ry2 = region
                if (x1 <= rx2 and x2 >= rx1 and y1 <= ry2 and y2 >= ry1):
                    to_merge.append(region)
                else:
                    remaining.append(region)
            
            for region in to_merge:
                rx1, ry1, rx2, ry2 = region
                x1 = min(x1, rx1)
                y1 = min(y1, ry1)
                x2 = max(x2, rx2)
                y2 = max(y2, ry2)
            
            merged.append((x1, y1, x2, y2))
            regions = remaining
        
        return merged
    
    def _filter_by_size(self, regions: List[Tuple[int, int, int, int]], img_size: Tuple[int, int]) -> List[Tuple[int, int, int, int]]:
        """Filter regions by size."""
        img_w, img_h = img_size
        img_area = img_w * img_h
        
        filtered = []
        for x1, y1, x2, y2 in regions:
            area = (x2 - x1) * (y2 - y1)
            area_ratio = area / img_area if img_area > 0 else 0
            
            if 0.005 < area_ratio < 0.3:
                filtered.append((max(0, x1), max(0, y1), min(img_w, x2), min(img_h, y2)))
        
        return filtered


class SmartWatermarkReplacer:
    """Intelligently replaces detected watermarks with new ones."""
    
    def __init__(self, font_path: Optional[str] = None):
        self.detector = AutomaticWatermarkDetector()
        self.font_path = font_path
        self._font_cache: Dict[int, ImageFont.FreeTypeFont] = {}
    
    def get_font(self, size: int = 20) -> ImageFont.FreeTypeFont:
        """Get font with caching."""
        if size not in self._font_cache:
            try:
                if self.font_path and os.path.exists(self.font_path):
                    self._font_cache[size] = ImageFont.truetype(self.font_path, size)
                else:
                    # Try DejaVu Sans (commonly available on Linux)
                    system_fonts = [
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                    ]
                    font_loaded = False
                    for sys_font in system_fonts:
                        if os.path.exists(sys_font):
                            self._font_cache[size] = ImageFont.truetype(sys_font, size)
                            font_loaded = True
                            break
                    
                    if not font_loaded:
                        logger.warning("No TTF font found, using default bitmap font")
                        self._font_cache[size] = ImageFont.load_default()
            except Exception as e:
                logger.warning(f"Failed to load font: {e}")
                self._font_cache[size] = ImageFont.load_default()
        
        return self._font_cache[size]
    
    def detect_and_replace_watermarks(
        self,
        img: Image.Image,
        new_watermark_text: str,
        position: str = "bottomright",
    ) -> Image.Image:
        """Detect and replace watermarks."""
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        watermark_regions = self.detector.detect_watermark_regions(img)
        
        if watermark_regions:
            logger.info(f"Found {len(watermark_regions)} watermark regions")
            img = self._remove_watermarks(img, watermark_regions)
        else:
            logger.info("No watermarks detected")
        
        img = self._add_new_watermark(img, new_watermark_text, position)
        
        return img
    
    def _remove_watermarks(self, img: Image.Image, regions: List[Tuple[int, int, int, int]]) -> Image.Image:
        """Remove detected watermarks."""
        draw = ImageDraw.Draw(img)
        img_array = np.array(img)
        
        for x1, y1, x2, y2 in regions:
            # Sample surrounding area
            surrounding_pixels = []
            
            # Sample from edges
            if y1 > 5:
                sample = img_array[y1-5:y1, x1:x2]
                surrounding_pixels.extend(sample.reshape(-1, 3).tolist())
            if y2 < img.height - 5:
                sample = img_array[y2:y2+5, x1:x2]
                surrounding_pixels.extend(sample.reshape(-1, 3).tolist())
            if x1 > 5:
                sample = img_array[y1:y2, x1-5:x1]
                surrounding_pixels.extend(sample.reshape(-1, 3).tolist())
            if x2 < img.width - 5:
                sample = img_array[y1:y2, x2:x2+5]
                surrounding_pixels.extend(sample.reshape(-1, 3).tolist())
            
            if surrounding_pixels:
                avg_color = tuple(int(sum(p[i] for p in surrounding_pixels) / len(surrounding_pixels)) for i in range(3))
            else:
                avg_color = (128, 128, 128)
            
            # Fill with gradient
            for y in range(y1, y2):
                for x in range(x1, x2):
                    dist_top = y - y1
                    dist_bottom = y2 - y
                    dist_left = x - x1
                    dist_right = x2 - x
                    
                    min_dist = min(dist_top, dist_bottom, dist_left, dist_right)
                    blend_factor = min(1.0, min_dist / 10.0)
                    
                    try:
                        actual_pixel = tuple(img_array[y, x].tolist())
                        blended = tuple(
                            int(actual_pixel[i] * (1 - blend_factor) + avg_color[i] * blend_factor)
                            for i in range(3)
                        )
                        draw.point((x, y), fill=blended)
                    except:
                        draw.point((x, y), fill=avg_color)
        
        return img
    
    def _add_new_watermark(
        self,
        img: Image.Image,
        text: str,
        position: str = "bottomright",
        opacity: int = 200,
    ) -> Image.Image:
        """Add new watermark."""
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        font_size = max(14, min(img.width, img.height) // 12)
        font = self.get_font(font_size)
        
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        padding = 15
        positions = {
            "topleft": (padding, padding),
            "topright": (img.width - text_width - padding, padding),
            "bottomleft": (padding, img.height - text_height - padding),
            "bottomright": (img.width - text_width - padding, img.height - text_height - padding),
            "center": ((img.width - text_width) // 2, (img.height - text_height) // 2),
        }
        
        x, y = positions.get(position.lower(), positions["bottomright"])
        
        outline_color = (0, 0, 0, 255)
        fill_color = (255, 255, 255, opacity)
        outline_width = max(1, font_size // 15)
        
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx != 0 or dy != 0:
                    draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        
        draw.text((x, y), text, font=font, fill=fill_color)
        
        img_rgba = img.convert('RGBA')
        combined = Image.alpha_composite(img_rgba, overlay)
        
        return combined.convert('RGB')
    
    def process_thumbnail(
        self,
        thumbnail_path: str,
        watermark_text: str,
        position: str = "bottomright",
    ) -> Optional[str]:
        """Process thumbnail with watermark replacement."""
        try:
            img = Image.open(thumbnail_path)
            
            processed_img = self.detect_and_replace_watermarks(
                img,
                watermark_text,
                position,
            )
            
            if processed_img.width > MAX_THUMBNAIL_SIZE or processed_img.height > MAX_THUMBNAIL_SIZE:
                processed_img.thumbnail((MAX_THUMBNAIL_SIZE, MAX_THUMBNAIL_SIZE), Image.Resampling.LANCZOS)
            
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
            processed_img.save(temp_file.name, 'JPEG', quality=85)
            
            return temp_file.name
        
        except Exception as e:
            logger.error(f"Error processing thumbnail: {e}", exc_info=True)
            return None


# Initialize processor
processor = SmartWatermarkReplacer(FONT_PATH)


# Bot command handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send usage instructions."""
    help_text = (
        "🎬 *Smart Video Thumbnail Watermark Bot*\n\n"
        "I automatically detect and replace watermarks on your video thumbnails!\n\n"
        "*Features:*\n"
        "• 🔍 Automatic watermark detection\n"
        "• 🎨 Smart content-aware removal\n"
        "• ✨ Beautiful new watermark with outline\n\n"
        "*Commands:*\n"
        "• `/setwatermark <text>` - Set your custom watermark text\n"
        "• `/setposition <position>` - Set watermark position\n"
        "  Positions: `topleft`, `topright`, `bottomleft`, `bottomright`, `center`\n"
        "• `/settings` - View your current settings\n"
        "• `/reset` - Clear all your settings\n\n"
        "*How to use:*\n"
        "1. Set your watermark text and position (or use defaults)\n"
        "2. Send me a video file\n"
        "3. I'll automatically detect old watermarks and replace them!"
    )
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def set_watermark_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set user's watermark text."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide watermark text!\nUsage: `/setwatermark Your Text`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    watermark_text = " ".join(context.args)
    context.user_data["watermark_text"] = watermark_text
    
    await update.message.reply_text(
        f"✅ Watermark text set to: *{watermark_text}*",
        parse_mode=ParseMode.MARKDOWN,
    )


async def set_position_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set user's watermark position."""
    valid_positions = ["topleft", "topright", "bottomleft", "bottomright", "center"]
    
    if not context.args or context.args[0].lower() not in valid_positions:
        positions_str = ", ".join(f"`{p}`" for p in valid_positions)
        await update.message.reply_text(
            f"❌ Please provide a valid position!\nValid: {positions_str}\nUsage: `/setposition bottomright`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    position = context.args[0].lower()
    context.user_data["position"] = position
    
    await update.message.reply_text(
        f"✅ Watermark position set to: *{position}*",
        parse_mode=ParseMode.MARKDOWN,
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current settings."""
    watermark_text = context.user_data.get("watermark_text", DEFAULT_WATERMARK)
    position = context.user_data.get("position", "bottomright")
    
    settings_text = (
        f"📋 *Current Settings*\n\n"
        f"• Watermark Text: *{watermark_text}*\n"
        f"• Position: *{position}*\n\n"
        f"🔍 *Detection:* Automatic"
    )
    
    await update.message.reply_text(settings_text, parse_mode=ParseMode.MARKDOWN)


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear user settings."""
    context.user_data.clear()
    await update.message.reply_text("🔄 All settings have been reset to defaults!")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming video files."""
    if not update.message.video:
        await update.message.reply_text("❌ Please send a video file!")
        return
    
    video = update.message.video
    
    if video.file_size > 50 * 1024 * 1024:
        await update.message.reply_text("❌ Video file is too large! Maximum size is 50MB.")
        return
    
    watermark_text = context.user_data.get("watermark_text", DEFAULT_WATERMARK)
    position = context.user_data.get("position", "bottomright")
    
    status_message = await update.message.reply_text("⏳ Processing your video... Please wait.")
    
    temp_files = []
    
    try:
        video_file = await context.bot.get_file(video.file_id)
        video_temp = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
        temp_files.append(video_temp.name)
        await video_file.download_to_drive(video_temp.name)
        
        if video.thumbnail:
            thumbnail_file = await context.bot.get_file(video.thumbnail.file_id)
            thumbnail_temp = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
            temp_files.append(thumbnail_temp.name)
            await thumbnail_file.download_to_drive(thumbnail_temp.name)
            
            processed_thumbnail = processor.process_thumbnail(
                thumbnail_temp.name,
                watermark_text,
                position,
            )
            
            if processed_thumbnail:
                temp_files.append(processed_thumbnail)
                
                await status_message.edit_text("📤 Uploading processed video...")
                
                with open(video_temp.name, 'rb') as video_stream:
                    await update.message.reply_video(
                        video=video_stream,
                        thumbnail=open(processed_thumbnail, 'rb'),
                        caption=f"✅ Video processed!\nNew watermark: {watermark_text}",
                    )
                
                await status_message.delete()
            else:
                await status_message.edit_text("❌ Failed to process thumbnail.")
        else:
            await status_message.edit_text("❌ This video doesn't have a thumbnail!")
    
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        await status_message.edit_text(f"❌ Error: {str(e)[:100]}")
    
    finally:
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
            except Exception as e:
                logger.error(f"Cleanup error: {e}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors."""
    logger.error(f"Error: {context.error}", exc_info=context.error)
    if update and update.effective_message:
        await update.effective_message.reply_text("❌ An error occurred. Please try again.")


def main() -> None:
    """Start the bot."""
    logger.info("Starting bot initialization...")
    
    try:
        # Test imports
        import numpy
        import scipy
        logger.info(f"NumPy version: {numpy.__version__}")
        logger.info(f"SciPy version: {scipy.__version__}")
        logger.info(f"Pillow version: {Image.__version__}")
    except Exception as e:
        logger.error(f"Import error: {e}")
        sys.exit(1)
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("setwatermark", set_watermark_command))
    application.add_handler(CommandHandler("setposition", set_position_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(MessageHandler(filters.VIDEO, handle_video))
    application.add_error_handler(error_handler)
    
    logger.info("🤖 Bot started successfully!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
