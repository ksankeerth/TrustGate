import time
from pathlib import Path

import pytest

from app.core.config import FaceMatchSettings
from app.layers.base import VerificationInput
from app.layers.face_match import RealFaceMatchLayer

EVAL_DIR = Path(__file__).resolve().parent.parent / "samples" / "face_match_eval"
SAME_PERSON_A = EVAL_DIR / "same_person_a.jpg"
SAME_PERSON_B = EVAL_DIR / "same_person_b.jpg"
DIFFERENT_PERSON = EVAL_DIR / "different_person.jpg"

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in (SAME_PERSON_A, SAME_PERSON_B, DIFFERENT_PERSON)),
    reason=(
        "no local eval images: add samples/face_match_eval/same_person_a.jpg, "
        "same_person_b.jpg, and different_person.jpg (gitignored, not committed) "
        "to run this test"
    ),
)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_real_face_match_same_person_low_risk_different_person_high_risk():
    layer = RealFaceMatchLayer(FaceMatchSettings(enabled=True))

    start = time.monotonic()
    same_person_result = await layer.run(
        VerificationInput(user_ref="eval-user", selfie=SAME_PERSON_A.read_bytes(), id_photo=SAME_PERSON_B.read_bytes())
    )
    different_person_result = await layer.run(
        VerificationInput(user_ref="eval-user", selfie=SAME_PERSON_A.read_bytes(), id_photo=DIFFERENT_PERSON.read_bytes())
    )
    elapsed = time.monotonic() - start

    print(
        f"same-person risk={same_person_result.risk:.3f} "
        f"different-person risk={different_person_result.risk:.3f} total={elapsed:.2f}s"
    )

    assert same_person_result.demonstrator is False
    assert same_person_result.risk < different_person_result.risk
