"""
GNFS Personnel Profile Picture Service
Handles image validation, sanitization, square crop, resizing, and secure storage.
"""

import base64
import io
import os
import uuid
from PIL import Image

from django.core.exceptions import ValidationError, PermissionDenied
from django.core.files.base import ContentFile
from django.db import transaction

from gnfs_komenda.models import NorminalRoll, AuditLog
from gnfs_komenda.services.permission_resolver import can_manage_personnel

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP'}
TARGET_SIZE = (400, 400)


def process_and_save_profile_picture(personnel, image_input, actor=None):
    """
    Validate, sanitize, square-crop, resize and save profile picture for Personnel.

    Args:
        personnel (NorminalRoll): Target officer record.
        image_input (UploadedFile, bytes, or base64 str): Input image file or data URI.
        actor (User, optional): Authenticated actor performing the change.

    Returns:
        NorminalRoll: Updated personnel record with profile_picture saved.
    """
    if not personnel:
        raise ValidationError("Target personnel record not found.")

    raw_data = None

    # Handle base64 Data URL (from client-side canvas crop)
    if isinstance(image_input, str):
        try:
            if image_input.startswith("data:"):
                header, base64_data = image_input.split(",", 1)
                raw_data = base64.b64decode(base64_data)
            else:
                raw_data = base64.b64decode(image_input)
        except Exception:
            raise ValidationError("Invalid or corrupted image data encoding.")
    elif hasattr(image_input, 'read'):
        raw_data = image_input.read()
    elif isinstance(image_input, bytes):
        raw_data = image_input
    else:
        raise ValidationError("Unsupported image data format.")

    if not raw_data:
        raise ValidationError("No image data provided.")

    if len(raw_data) > MAX_FILE_SIZE:
        raise ValidationError(f"Image file size exceeds maximum allowed limit of {MAX_FILE_SIZE // (1024 * 1024)} MB.")

    # Validate image using Pillow
    try:
        img = Image.open(io.BytesIO(raw_data))
        img.verify()
        # Re-open after verify() as recommended by Pillow documentation
        img = Image.open(io.BytesIO(raw_data))
    except Exception as e:
        raise ValidationError(f"Invalid or corrupted image file: {str(e)}")

    img_format = img.format.upper() if img.format else "JPEG"
    if img_format not in ALLOWED_FORMATS:
        raise ValidationError(f"Unsupported image format: {img_format}. Allowed formats: JPEG, PNG, WebP.")

    # Convert to RGB (handles RGBA, P, etc.)
    if img.mode in ("RGBA", "P", "LA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Center-crop to 1:1 square if not already square
    width, height = img.size
    if width != height:
        min_dim = min(width, height)
        left = (width - min_dim) // 2
        top = (height - min_dim) // 2
        right = left + min_dim
        bottom = top + min_dim
        img = img.crop((left, top, right, bottom))

    # Resize to standard high-resolution avatar dimension
    img = img.resize(TARGET_SIZE, Image.Resampling.LANCZOS)

    # Save to JPEG in memory buffer
    output_buffer = io.BytesIO()
    img.save(output_buffer, format="JPEG", quality=90, optimize=True)
    output_buffer.seek(0)

    # Safe unique filename using staffID and short UUID
    safe_staff_id = (personnel.staffID or "GNFS").replace("/", "-").strip()
    safe_filename = f"avatar_{safe_staff_id}_{uuid.uuid4().hex[:8]}.jpg"

    with transaction.atomic():
        # Clean up old profile picture file if exists
        if personnel.profile_picture:
            try:
                old_path = personnel.profile_picture.path
                if os.path.isfile(old_path):
                    os.remove(old_path)
            except Exception:
                pass

        personnel.profile_picture.save(
            safe_filename,
            ContentFile(output_buffer.getvalue()),
            save=True
        )

        AuditLog.objects.create(
            user=actor,
            action="PROFILE_PICTURE_UPDATED",
            target_type="NorminalRoll",
            target_id=str(personnel.id),
            description=f"Updated profile photo for {personnel.fullname} ({personnel.staffID}).",
            station=personnel.station,
            district=personnel.current_district,
            region=personnel.current_region
        )

    return personnel


def can_user_manage_profile_picture(user, personnel):
    """
    Check authorization to manage a personnel's profile photo:
    - User can manage own photo.
    - Super Admin can manage nationwide.
    - Regional Admin can manage officers within their assigned region.
    - District Admin can manage officers within their assigned district.
    """
    if not user or not user.is_authenticated or not personnel:
        return False
    if user.is_superuser:
        return True

    from gnfs_komenda.security import get_personnel_for_user
    own_personnel = get_personnel_for_user(user)
    if own_personnel and own_personnel.id == personnel.id:
        return True

    return can_manage_personnel(user, personnel)
