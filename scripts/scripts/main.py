
from typing import Union
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from util.inference_utils import inference, create_model, ImageDecodeError
from typing import List, Optional
from pydantic import BaseModel
from fastapi import UploadFile, File
import io
from starlette.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import numpy as np
import cv2
from PIL import Image
import base64
import math
import re
import threading
from functools import lru_cache


def _default_gpu_ids() -> List[int]:
    """Use GPU 0 only when CUDA is available: init_net() asserts torch.cuda.is_available()."""
    try:
        import torch
        return [0] if torch.cuda.is_available() else []
    except Exception:
        return []


class Parameters(BaseModel):
    input_nc: int = 3
    output_nc: int = 3
    ngf: int = 64
    ndf: int = 64
    netG:str = 'resnet_9blocks'
    norm: str = 'instance'
    init_type: str = 'xavier'
    init_gain: float = 0.02
    display_sides: int = 1
    num_classes: int = 1
    gpu_ids: List[int] = Field(default_factory=_default_gpu_ids)
    isTrain: bool = False


app = FastAPI()
origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,  # a wildcard origin must not be combined with credentials
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"Hello": "World"}

def from_image_to_bytes(img):
    """
    pillow image to bytes
    """
    imgByteArr = io.BytesIO()
    img.save(imgByteArr, format='png')
    imgByteArr = imgByteArr.getvalue()

    encoded = base64.b64encode(imgByteArr)
    decoded = encoded.decode('ascii')
    return decoded

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# inference() does not convert units: it only prints the unit next to the measured length and
# width with cv2.putText, which can draw ASCII only. The web UI lets the user type any unit, so
# common Cyrillic / Greek spellings are mapped to ASCII first, and then any short Latin label
# ('px', 'mm', 'cm', 'm', 'in', ...) is accepted instead of a fixed list.
_UNIT_ALIASES = {
    'мм': 'mm', 'см': 'cm', 'м': 'm', 'дм': 'dm', 'км': 'km',
    '\u00b5m': 'um', '\u03bcm': 'um',  # micro sign and Greek small mu
}
_UNIT_RE = re.compile(r'[A-Za-z0-9]{1,8}')
# Plain decimals only: "10", "5.5", ".5", "2." (no sign, exponent, "nan", "inf" or digit separators)
_DIM_RE = re.compile(r'(?:[0-9]+\.?[0-9]*|\.[0-9]+)-(?:[0-9]+\.?[0-9]*|\.[0-9]+)')


def parse_dimensions(dim: str):
    """'W-H' -> (width, height) as positive finite floats, otherwise HTTP 422."""
    if _DIM_RE.fullmatch(dim) is None:
        raise HTTPException(status_code=422, detail="dim must look like 'W-H', e.g. '10-5.5' (two positive numbers)")
    width, height = [float(x) for x in dim.split('-')]
    if not (math.isfinite(width) and math.isfinite(height)) or width <= 0 or height <= 0:
        raise HTTPException(status_code=422, detail="dim must contain two positive finite numbers")
    return width, height


def normalize_unit(unit: str) -> str:
    value = unit.strip()
    value = _UNIT_ALIASES.get(value.lower(), value)  # case-insensitive lookup of the common spellings
    if _UNIT_RE.fullmatch(value) is None:
        raise HTTPException(
            status_code=422,
            detail="unit must be 1-8 Latin letters or digits, e.g. 'px', 'mm', 'cm', 'm'. Please use Latin letters.",
        )
    return value


@lru_cache(maxsize=1)
def get_model():
    """Build the model once (lazily) and reuse it: loading the checkpoint per request is slow."""
    return create_model(Parameters())


# The shared model is stateful (set_input / test / get_current_visuals) and requests run in worker
# threads, so building and using the model happens one request at a time.
_inference_lock = threading.Lock()

#python-multipart
@app.post("/predict/{dim}/{unit}")
def predict(dim:str, unit:str, file: UploadFile = File(...)):
    # A plain "def": FastAPI runs it in a worker thread, so the heavy model work does not block the
    # event loop (and with it every other request) while it runs.
    width, height = parse_dimensions(dim)
    unit = normalize_unit(unit)
    # Read one byte more than the limit to detect oversized uploads without loading all of them
    bytesImg = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(bytesImg) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Image is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        with _inference_lock:
            model = get_model()  # inside the lock: two first requests must not both load the checkpoint
            predicted_cntr, visuals = inference(model, bytesImg, (width, height), unit) # Outputs Predicted Masks
    except ImageDecodeError as exc:
        raise HTTPException(status_code=415, detail="The uploaded file is not a decodable image") from exc

    cntr_converted = from_image_to_bytes(Image.fromarray(predicted_cntr))
    
    img_list = [cntr_converted]
    
    for k in ['fused', 'side1', 'side2', 'side3', 'side4', 'side5']:
        map_ = from_image_to_bytes(Image.fromarray(visuals[k]))
        img_list.append(map_)
    return img_list