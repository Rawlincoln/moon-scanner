"""Shared clients and learning engines for the FastAPI app."""

from __future__ import annotations

from services.analyze_token import bind_learning as bind_analyze_learning
from services.learning.memory import LearningMemory
from services.learning.tracker import LearningEngine
from services.padre import PadreClient
from services.pumpfun import PumpFunClient
from services.scan_moon import bind_learning as bind_moon_learning
from services.scan_moon import init_outcomes
from services.scan_trenches import bind_learning_memory

from app.paths import BASE_DIR, DATA_DIR
from config import LEARNING_DB

pump = PumpFunClient()
padre = PadreClient()

learning_memory = LearningMemory(LEARNING_DB)
learning = LearningEngine(learning_memory)


def init_shared() -> None:
    """Bind learning + outcomes once at app creation."""
    init_outcomes(BASE_DIR)  # resolves MOON_OUTCOMES_DB via config/DATA_DIR
    bind_analyze_learning(learning)
    bind_moon_learning(learning)
    bind_learning_memory(learning_memory)
