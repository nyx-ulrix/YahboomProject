"""
VIT / MobileCLIP scene-decoder package.

Exposes the global ``vit_service`` singleton used by the Flask routes.
"""

from app.services.vit.vit_service import vit_service

__all__ = ["vit_service"]
