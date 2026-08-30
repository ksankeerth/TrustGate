# samples/

Deliberately empty of images. Real face photos and deepfake samples are not
committed to this repo, for the obvious privacy and licensing reasons.

`scripts/demo.sh` no longer needs anything here -- it generates synthetic
capture images (noise plus camera EXIF) into a temp directory at run time.

Two gitignored subdirectories are where you put your own images to run the
`slow` tests, which skip cleanly when the files are absent:

| Directory | Files | Used by |
|---|---|---|
| `deepfake_eval/` | `known_real.jpg`, `known_fake.jpg` | `tests/test_deepfake_real.py` |
| `face_match_eval/` | `same_person_a.jpg`, `same_person_b.jpg`, `different_person.jpg` | `tests/test_face_match_real.py` |

Add your own, then run `pytest -m slow`.
