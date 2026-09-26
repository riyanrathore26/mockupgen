"""mockupgen — from-scratch PSD smart-object mockup engine."""

__version__ = "0.3.0"

from mockupgen.api import Mockup
from mockupgen.log import setup_logging

__all__ = ["Mockup", "setup_logging", "__version__"]
