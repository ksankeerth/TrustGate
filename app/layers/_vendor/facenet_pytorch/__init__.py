"""Vendored subset of facenet-pytorch v2.6.0 (MIT license, see LICENSE.md).

Only what RealFaceMatchLayer needs: face detection/cropping (MTCNN) and
face embedding (InceptionResnetV1). See README.md for why this is vendored
instead of an ordinary dependency.
"""

from .models.inception_resnet_v1 import InceptionResnetV1
from .models.mtcnn import MTCNN
