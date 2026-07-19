#!/usr/bin/env python3
"""
Telegram Bot for Video Thumbnail Automatic Watermark Replacement
Supports all file sizes - processes thumbnails only for large files.
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
MAX_FILE_SIZE_FOR_REUPLOAD = 45 * 1024 * 1024  # 45MB safe limit for re-upload


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
        if corner_regions:
            regions.extend(corner_regions)
        
        # Method 2: Text region detection
        text_regions = self._detect_text_regions(img_array)
        if text_regions:
            regions.extend(text_regions)
        
        # Method 3: Uniform region detection
        uniform_regions = self._detect_uniform_regions(img_array)
        if uniform_regions:
            regions.extend(uniform_regions)
        
        # Merge overlapping regions
        if regions:
            merged_regions = self._merge_overlapping_regions(regions)
            # Filter by size
            filtered_regions = self._filter_by_size(merged_regions, img.size)
            return filtered_regions
        
        return []
    
    def _detect_text_regions(self, img_array: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """Detect text-like regions using edge detection."""
        regions = []
        
        try:
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
                            # Add padding
                            regions.append((
                                max(0, x1-5), max(0, y1-5),
                                min(img_array.shape[1], x2+5),
                                min(img_array.shape[0], y2+5)
                            ))
        except Exception as e:
            logger.debug(f"Text detection error: {e}")
        
        return regions
    
    def _detect_uniform_regions(self, img_array: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """Detect uniform colored regions (logo watermarks)."""
        regions = []
        
        try:
            if len(img_array.shape) == 3:
                h, w = img_array.shape[:2]
                
                for scale in [0.1, 0.15]:
                    window_h = int(h * scale)
                    window_w = int(w * scale)
                    
                    if window_h < 10 or window_w < 10:
                        continue
                    
                    # Sample at intervals
                    for y in range(0, h - window_h, window_h // 2):
                        for x in range(0, w - window_w, window_w // 2):
                            window = img_array[y:y+window_h, x:x+window_w, :3]
                            variance = np.var(window)
                            
                            # Low variance = uniform color
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
        except Exception as e:
            logger.debug(f"Uniform region detection error: {e}")
        
        return regions
    
    def _detect_corner_watermarks(self, img_array: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """Detect watermarks commonly placed in corners."""
        regions = []
        
        try:
            h, w = img_array.shape[:2]
            
            corner_size_h = int(h * 0.25)
            corner_size_w = int(w * 0.25)
            
            corners = [
                (0, 0, corner_size_w, corner_size_h),  # Top-left
                (w - corner_size_w, 0, w, corner_size_h),  # Top-right
                (0, h - corner_size_h, corner_size_w, h),  # Bottom-left
                (w - corner_size_w, h - corner_size_h, w, h),  # Bottom-right
            ]
            
            for x1, y1, x2, y2 in corners:
                if x2 <= x1 or y2 <= y1:
                    continue
                    
                corner_region = img_array[y1:y2, x1:x2, :3]
                
                # Get main region (center of image)
                center_y1 = corner_size_h
                center_y2 = h - corner_size_h
                center_x1 = corner_size_w
                center_x2 = w - corner_size_w
                
                if center_y2 > center_y1 and center_x2 > center_x1:
                    main_region = img_array[center_y1:center_y2, center_x1:center_x2, :3]
                    
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
                                    if len(ys) > 0 and len(xs) > 0:
                                        exact_y1 = y1 + ys.min()
                                        exact_y2 = y1 + ys.max()
                                        exact_x1 = x1 + xs.min()
                                        exact_x2 = x1 + xs.max()
                                        
                                        if exact_x2 > exact_x1 and exact_y2 > exact_y1:
                                            regions.append((exact_x1, exact_y1, exact_x2, exact_y2))
        except Exception as e:
            logger.debug(f"Corner detection error: {e}")
        
        return regions
    
    def _merge_overlapping_regions(self, regions: List[Tuple[int, int, int, int]]) -> List[Tuple[int, int, int, int]]:
        """Merge overlapping bounding boxes."""
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
        """Filter regions by size relative to image."""
        img_w, img_h = img_size
        img_area = img_w * img_h
        
        if img_area == 0:
            return []
        
        filtered = []
        for x1, y1, x2, y2 in regions:
            area = (x2 - x1) * (y2 - y1)
            area_ratio = area / img_area
            
            # Watermarks typically occupy 0.5% to 30% of image
            if 0.005 < area_ratio < 0.3:
                filtered.append((
                    max(0, x1),
                    max(0, y1),
                    min(img_w, x2),
                    min(img_h, y2)
                ))
        
        return filtered


class SmartWatermarkReplacer:
    """Intelligently replaces detected watermarks with new ones."""
    
    def __init__(self, font_path: Optional[str] = None):
        self.detector = AutomaticWatermarkDetector()
        self.font_path = font_path
        self._font_cache: Dict[int, ImageFont.FreeTypeFont] = {}
    
    def get_font(self, size: int = 20) -> ImageFont.FreeTypeFont:
        """Get font with caching by size."""
        if size not in self._font_cache:
            try:
                if self.font_path and os.path.exists(self.font_path):
                    self._font_cache[size] = ImageFont.truetype(self.font_path, size)
                else:
                    # Try Linux system fonts
                    system_fonts = [
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                    ]
                    font_loaded = False
                    for sys_font in system_fonts:
                        if os.path.exists(sys_font):
                            try:
                                self._font_cache[size] = ImageFont.truetype(sys_font, size)
                                font_loaded = True
                                break
                            except:
                                continue
                    
                    if not font_loaded:
                        logger.warning("No TTF font found, using default bitmap font")
                        self._font_cache[size] = ImageFont.load_default()
            except Exception as e:
                logger.warning(f"Failed to load font: {e}. Using default.")
                self._font_cache[size] = ImageFont.load_default()
        
        return self._font_cache[size]
    
    def detect_and_replace_watermarks(
        self,
        img: Image.Image,
        new_watermark_text: str,
        position: str = "bottomright",
    ) -> Image.Image:
        """Detect old watermarks, remove them, and add new watermark."""
        # Convert to RGB
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Detect and remove old watermarks
        logger.info("Detecting watermarks...")
        watermark_regions = self.detector.detect_watermark_regions(img)
        
        if watermark_regions:
            logger.info(f"Found {len(watermark_regions)} watermark region(s), removing...")
            img = self._remove_watermarks(img, watermark_regions)
        else:
            logger.info("No watermarks detected")
        
        # Add new watermark
        logger.info("Adding new watermark...")
        img = self._add_new_watermark(img, new_watermark_text, position)
        
        return img
    
    def _remove_watermarks(self, img: Image.Image, regions: List[Tuple[int, int, int, int]]) -> Image.Image:
        """Remove detected watermarks using content-aware fill."""
        draw = ImageDraw.Draw(img)
        img_array = np.array(img)
        
        for x1, y1, x2, y2 in regions:
            # Ensure valid coordinates
            x1 = max(0, min(x1, img.width - 1))
            y1 = max(0, min(y1, img.height - 1))
            x2 = max(x1 + 1, min(x2, img.width))
            y2 = max(y1 + 1, min(y2, img.height))
            
            # Sample surrounding area for fill color
            surrounding_pixels = []
            
            # Sample from above
            if y1 > 3:
                sample_y1 = max(0, y1 - 3)
                sample = img_array[sample_y1:y1, x1:x2]
                if sample.size > 0:
                    surrounding_pixels.extend(sample.reshape(-1, 3).tolist())
            
            # Sample from below
            if y2 < img.height - 3:
                sample_y2 = min(img.height, y2 + 3)
                sample = img_array[y2:sample_y2, x1:x2]
                if sample.size > 0:
                    surrounding_pixels.extend(sample.reshape(-1, 3).tolist())
            
            # Sample from left
            if x1 > 3:
                sample_x1 = max(0, x1 - 3)
                sample = img_array[y1:y2, sample_x1:x1]
                if sample.size > 0:
                    surrounding_pixels.extend(sample.reshape(-1, 3).tolist())
            
            # Sample from right
            if x2 < img.width - 3:
                sample_x2 = min(img.width, x2 + 3)
                sample = img_array[y1:y2, x2:sample_x2]
                if sample.size > 0:
                    surrounding_pixels.extend(sample.reshape(-1, 3).tolist())
            
            # Calculate average fill color
            if surrounding_pixels:
                avg_color = tuple(
                    int(sum(p[i] for p in surrounding_pixels) / len(surrounding_pixels))
                    for i in range(3)
                )
            else:
                avg_color = (128, 128, 128)  # Fallback gray
            
            # Fill region with gradient blending
            for y in range(y1, y2):
                for x in range(x1, x2):
                    dist_top = y - y1
                    dist_bottom = y2 - y
                    dist_left = x - x1
                    dist_right = x2 - x
                    
                    min_dist = min(dist_top, dist_bottom, dist_left, dist_right)
                    blend_factor = min(1.0, min_dist / 10.0)
                    
                    try:
                        actual_pixel = tuple(img_array[y, x, :3].tolist())
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
    ) -> Image.Image:
        """Add new watermark text with outline."""
        # Create transparent overlay
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        # Calculate font size
        font_size = max(14, min(img.width, img.height) // 12)
        font = self.get_font(font_size)
        
        # Get text dimensions
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # Calculate position
        padding = 15
        positions = {
            "topleft": (padding, padding),
            "topright": (img.width - text_width - padding, padding),
            "bottomleft": (padding, img.height - text_height - padding),
            "bottomright": (img.width - text_width - padding, img.height - text_height - padding),
            "center": ((img.width - text_width) // 2, (img.height - text_height) // 2),
        }
        
        x, y = positions.get(position.lower(), positions["bottomright"])
        
        # Ensure text stays within image bounds
        x = max(0, min(x, img.width - text_width))
        y = max(0, min(y, img.height - text_height))
        
        # Draw text with outline
        outline_color = (0, 0, 0, 255)  # Black outline
        fill_color = (255, 255, 255, 200)  # Semi-transparent white
        outline_width = max(1, font_size // 15)
        
        # Draw outline
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx != 0 or dy != 0:
                    draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        
        # Draw main text
        draw.text((x, y), text, font=font, fill=fill_color)
        
        # Composite overlay onto image
        img_rgba = img.convert('RGBA')
        combined = Image.alpha_composite(img_rgba, overlay)
        
        return combined.convert('RGB')
    
    def process_thumbnail(
        self,
        thumbnail_path: str,
        watermark_text: str,
        position: str = "bottomright",
    ) -> Optional[str]:
        """Process thumbnail: detect, remove old watermark, add new one."""
        try:
            # Open image
            img = Image.open(thumbnail_path)
            logger.info(f"Processing thumbnail: {img.size}, mode: {img.mode}")
            
            # Process watermark replacement
            processed_img = self.detect_and_replace_watermarks(
                img,
                watermark_text,
                position,
            )
            
            # Resize if needed (Telegram limit: 320x320)
            if processed_img.width > MAX_THUMBNAIL_SIZE or processed_img.height > MAX_THUMBNAIL_SIZE:
                processed_img.thumbnail(
                    (MAX_THUMBNAIL_SIZE, MAX_THUMBNAIL_SIZE),
                    Image.Resampling.LANCZOS
                )
                logger.info(f"Resized thumbnail to: {processed_img.size}")
            
            # Save to temporary file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
            processed_img.save(temp_file.name, 'JPEG', quality=85)
            logger.info(f"Saved processed thumbnail to: {temp_file.name}")
            
            return temp_file.name
        
        except Exception as e:
            logger.error(f"Error processing thumbnail: {e}", exc_info=True)
            return None


# Initialize processor
processor = SmartWatermarkReplacer(FONT_PATH)


# ==================== BOT COMMAND HANDLERS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send usage instructions when /start is issued."""
    help_text = (
        "🎬 *Smart Video Thumbnail Watermark Bot*\n\n"
        "I automatically detect and replace watermarks on your video thumbnails!\n\n"
        "*Features:*\n"
        "• 🔍 Automatic watermark detection\n"
        "• 🎨 Smart content-aware removal\n"
        "• ✨ Beautiful new watermark with outline\n"
        "• 📁 Works with any file size\n\n"
        "*Commands:*\n"
        "• `/setwatermark <text>` - Set your custom watermark text\n"
        "• `/setposition <position>` - Set watermark position\n"
        "  Positions: `topleft`, `topright`, `bottomleft`, `bottomright`, `center`\n"
        "• `/settings` - View your current settings\n"
        "• `/reset` - Clear all your settings\n\n"
        "*How to use:*\n"
        "1. Set your watermark text and position (or use defaults)\n"
        "2. Send me any video file (any size!)\n"
        "3. I'll automatically process the thumbnail\n"
        "4. For videos under 45MB: Get video back with new thumbnail\n"
        "5. For larger videos: Get processed thumbnail image\n\n"
        f"📌 *Default watermark:* `{DEFAULT_WATERMARK}`\n"
        f"📌 *Default position:* `bottomright`"
    )
    
    await update.message.reply_text(
        help_text,
        parse_mode=ParseMode.MARKDOWN,
    )


async def set_watermark_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set user's watermark text."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide watermark text!\n"
            "Usage: `/setwatermark Your Text`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    watermark_text = " ".join(context.args)
    
    # Limit watermark length
    if len(watermark_text) > 100:
        await update.message.reply_text(
            "❌ Watermark text too long! Maximum 100 characters.",
        )
        return
    
    context.user_data["watermark_text"] = watermark_text
    
    await update.message.reply_text(
        f"✅ Watermark text set to: *{watermark_text}*",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info(f"User {update.effective_user.id} set watermark: {watermark_text}")


async def set_position_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set user's watermark position."""
    valid_positions = ["topleft", "topright", "bottomleft", "bottomright", "center"]
    
    if not context.args or context.args[0].lower() not in valid_positions:
        positions_str = ", ".join(f"`{p}`" for p in valid_positions)
        await update.message.reply_text(
            f"❌ Please provide a valid position!\n"
            f"Valid positions: {positions_str}\n"
            f"Usage: `/setposition bottomright`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    position = context.args[0].lower()
    context.user_data["position"] = position
    
    await update.message.reply_text(
        f"✅ Watermark position set to: *{position}*",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info(f"User {update.effective_user.id} set position: {position}")


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current user settings."""
    watermark_text = context.user_data.get("watermark_text", DEFAULT_WATERMARK)
    position = context.user_data.get("position", "bottomright")
    
    settings_text = (
        "📋 *Current Settings*\n\n"
        f"• Watermark Text: *{watermark_text}*\n"
        f"• Position: *{position}*\n\n"
        "🔍 *Detection:* Automatic\n"
        "📁 *File Support:* Any size"
    )
    
    await update.message.reply_text(
        settings_text,
        parse_mode=ParseMode.MARKDOWN,
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear user settings."""
    context.user_data.clear()
    
    await update.message.reply_text(
        "🔄 All settings have been reset to defaults!\n\n"
        f"• Watermark: `{DEFAULT_WATERMARK}`\n"
        "• Position: `bottomright`",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info(f"User {update.effective_user.id} reset settings")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming video files - supports all sizes."""
    if not update.message or not update.message.video:
        return
    
    video = update.message.video
    user_id = update.effective_user.id
    
    # Get user settings
    watermark_text = context.user_data.get("watermark_text", DEFAULT_WATERMARK)
    position = context.user_data.get("position", "bottomright")
    
    # Log incoming video
    file_size_mb = video.file_size / (1024 * 1024)
    logger.info(f"User {user_id} sent video: {video.file_name} ({file_size_mb:.1f} MB)")
    
    # Determine if we can re-upload the full video
    can_reupload = video.file_size <= MAX_FILE_SIZE_FOR_REUPLOAD
    
    # Send initial status
    if can_reupload:
        status_text = (
            "⏳ *Processing video...*\n"
            f"• Size: {file_size_mb:.1f} MB\n"
            "• Mode: Full video processing\n"
            "• Detecting watermarks...\n\n"
            "Please wait..."
        )
    else:
        status_text = (
            "📁 *Large file detected!*\n"
            f"• Size: {file_size_mb:.1f} MB\n"
            "• Mode: Thumbnail-only processing\n"
            "• Reason: File exceeds 45MB re-upload limit\n\n"
            "⏳ Processing thumbnail..."
        )
    
    status_message = await update.message.reply_text(
        status_text,
        parse_mode=ParseMode.MARKDOWN,
    )
    
    temp_files = []
    
    try:
        if not video.thumbnail:
            await status_message.edit_text(
                "❌ This video doesn't have a thumbnail!\n"
                "I can only process videos that already have thumbnails.\n\n"
                "💡 *Tip:* Add a thumbnail to your video before sending.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        
        # Download thumbnail (always small, <100KB)
        thumbnail_file = await context.bot.get_file(video.thumbnail.file_id)
        thumbnail_temp = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
        temp_files.append(thumbnail_temp.name)
        await thumbnail_file.download_to_drive(thumbnail_temp.name)
        
        # Update status
        await status_message.edit_text(
            "🔍 *Analyzing thumbnail...*",
            parse_mode=ParseMode.MARKDOWN,
        )
        
        # Process the thumbnail
        processed_thumbnail = processor.process_thumbnail(
            thumbnail_temp.name,
            watermark_text,
            position,
        )
        
        if not processed_thumbnail:
            await status_message.edit_text(
                "❌ Failed to process thumbnail.\n"
                "Please try again with a different video.",
            )
            return
        
        temp_files.append(processed_thumbnail)
        
        if can_reupload:
            # For smaller files: download video, send back with new thumbnail
            await status_message.edit_text(
                "📥 *Downloading video...*",
                parse_mode=ParseMode.MARKDOWN,
            )
            
            video_file = await context.bot.get_file(video.file_id)
            video_temp = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
            temp_files.append(video_temp.name)
            await video_file.download_to_drive(video_temp.name)
            
            await status_message.edit_text(
                "📤 *Uploading processed video...*",
                parse_mode=ParseMode.MARKDOWN,
            )
            
            # Send video back with new thumbnail
            with open(video_temp.name, 'rb') as video_stream:
                await update.message.reply_video(
                    video=video_stream,
                    thumbnail=open(processed_thumbnail, 'rb'),
                    caption=(
                        f"✅ *Video Processed!*\n\n"
                        f"🏷️ Watermark: *{watermark_text}*\n"
                        f"📍 Position: *{position}*"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                    supports_streaming=True,
                )
            
            await status_message.delete()
            logger.info(f"Successfully processed full video for user {user_id}")
        
        else:
            # For large files: send only the thumbnail with info
            await status_message.edit_text(
                "📤 *Uploading processed thumbnail...*",
                parse_mode=ParseMode.MARKDOWN,
            )
            
            with open(processed_thumbnail, 'rb') as thumb_file:
                await update.message.reply_photo(
                    photo=thumb_file,
                    caption=(
                        f"✅ *Thumbnail Processed Successfully!*\n\n"
                        f"📹 Original: `{video.file_name}`\n"
                        f"📏 Size: `{file_size_mb:.1f} MB`\n"
                        f"🏷️ New watermark: *{watermark_text}*\n"
                        f"📍 Position: *{position}*\n\n"
                        f"⚠️ *Note:* Video too large to re-upload\n"
                        f"(Telegram bot limit: 50MB)\n\n"
                        f"💡 *Solutions:*\n"
                        f"• Use this thumbnail for your video manually\n"
                        f"• Compress video to under 50MB\n"
                        f"• Enable large files in @BotFather"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            
            await status_message.delete()
            logger.info(f"Processed thumbnail-only for large video ({file_size_mb:.1f} MB)")
    
    except Exception as e:
        logger.error(f"Error processing video: {e}", exc_info=True)
        error_message = str(e)[:200]
        
        await status_message.edit_text(
            f"❌ *Error processing video*\n\n"
            f"```{error_message}```\n\n"
            f"Please try again or contact support.",
            parse_mode=ParseMode.MARKDOWN,
        )
    
    finally:
        # Clean up all temporary files
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
                    logger.debug(f"Cleaned up: {temp_file}")
            except Exception as e:
                logger.error(f"Failed to clean up {temp_file}: {e}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors in the bot."""
    logger.error(
        f"Update {update} caused error {context.error}",
        exc_info=context.error,
    )
    
    # Send message to user if possible
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ An unexpected error occurred.\n"
                "Please try again later or use /start to restart.",
            )
        except Exception:
            pass


def main() -> None:
    """Start the bot with proper initialization."""
    logger.info("=" * 50)
    logger.info("Starting Telegram Watermark Bot")
    logger.info("=" * 50)
    
    # Verify imports
    try:
        logger.info(f"NumPy version: {np.__version__}")
        logger.info(f"SciPy version: {ndimage.__version__ if hasattr(ndimage, '__version__') else 'installed'}")
        logger.info(f"Pillow version: {Image.__version__}")
    except Exception as e:
        logger.error(f"Import verification failed: {e}")
        sys.exit(1)
    
    # Verify bot token
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        sys.exit(1)
    
    logger.info(f"Bot token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-5:]}")
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Register command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("setwatermark", set_watermark_command))
    application.add_handler(CommandHandler("setposition", set_position_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("reset", reset_command))
    
    # Register video handler
    application.add_handler(MessageHandler(filters.VIDEO, handle_video))
    
    # Register error handler
    application.add_error_handler(error_handler)
    
    logger.info("All handlers registered successfully")
    logger.info("🤖 Bot is starting...")
    
    # Start polling
    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}", exc_info=True)
    finally:
        logger.info("Bot shutdown complete")


if __name__ == "__main__":
    main()
