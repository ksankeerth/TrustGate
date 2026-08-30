import time
from pathlib import Path

import pytest

from app.core.config import DEEPFAKE_MODEL_CHECKPOINTS, DeepfakeSettings
from app.layers.base import VerificationInput
from app.layers.deepfake import RealDeepfakeLayer

EVAL_DIR = Path(__file__).resolve().parent.parent / "samples" / "deepfake_eval"
KNOWN_REAL_PATH = EVAL_DIR / "known_real.jpg"
KNOWN_FAKE_PATH = EVAL_DIR / "known_fake.jpg"

pytestmark = pytest.mark.skipif(
    not (KNOWN_REAL_PATH.exists() and KNOWN_FAKE_PATH.exists()),
    reason=(
        "no local eval images: add samples/deepfake_eval/known_real.jpg and "
        "known_fake.jpg (gitignored, not committed) to run this test"
    ),
)


@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.parametrize("model_choice", sorted(DEEPFAKE_MODEL_CHECKPOINTS))
async def test_real_deepfake_orders_known_real_below_known_fake(model_choice):
    layer = RealDeepfakeLayer(DeepfakeSettings(model_choice=model_choice, enabled=True))

    real_input = VerificationInput(user_ref="eval-user", selfie=KNOWN_REAL_PATH.read_bytes())
    fake_input = VerificationInput(user_ref="eval-user", selfie=KNOWN_FAKE_PATH.read_bytes())

    start = time.monotonic()
    real_result = await layer.run(real_input)
    fake_result = await layer.run(fake_input)
    elapsed = time.monotonic() - start

    print(
        f"[{model_choice}] real risk={real_result.risk:.3f} "
        f"fake risk={fake_result.risk:.3f} total={elapsed:.2f}s"
    )

    assert real_result.demonstrator is False
    assert fake_result.risk > real_result.risk
