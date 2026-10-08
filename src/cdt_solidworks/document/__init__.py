"""SolidWorks document lifecycle/query service seam."""

from .models import BodyInfo, ComponentInfo, DocumentContext, DocumentInfo, DocumentType, FeatureInfo
from .path_policy import DocumentPathPolicy
from .service import DocumentService

__all__ = [
    "BodyInfo",
    "ComponentInfo",
    "DocumentContext",
    "DocumentInfo",
    "DocumentPathPolicy",
    "DocumentService",
    "DocumentType",
    "FeatureInfo",
]
