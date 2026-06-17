import os
import cv2
import torch
import numpy as np
import math
from PIL import Image
from io import BytesIO
import torchvision.transforms as transforms
from scipy.spatial import distance as dist
from imutils import perspective
from imutils import contours
import imutils

from .models.deepcrack_model import DeepCrackModel

# --- БЕЗОПАСНЫЙ ИСПРАВЛЕННЫЙ АЛГОРИТМ РАЗМЕТКИ КОНТУРОВ ---

def midpoint(ptA, ptB):
    return ((ptA[0] + ptB[0]) * 0.5, (ptA[1] + ptB[1]) * 0.5)

def extract_bboxes(fused):
    mask = cv2.cvtColor(fused, cv2.COLOR_BGR2GRAY)
    mask[mask < 40] = 0
    mask[mask >= 40] = 1
    mask = mask.reshape(256, 256, 1)
    boxes = np.zeros([mask.shape[-1], 4], dtype=np.int32)
    for i in range(mask.shape[-1]):
        m = mask[:, :, i]
        horizontal_indicies = np.where(np.any(m, axis=0))[0]
        vertical_indicies = np.where(np.any(m, axis=1))[0]
        if horizontal_indicies.shape[0]:
            x1, x2 = horizontal_indicies[[0, -1]]
            y1, y2 = vertical_indicies[[0, -1]]
            x2 += 1
            y2 += 1
        else:
            x1, x2, y1, y2 = 0, 0, 0, 0
        boxes[i] = np.array([y1, x1, y2, x2])
    return boxes.astype(np.int32)

def getContours(npImage, overlay_img, realHeight, realWidth, unit, confidence, angle_th=30):
    image = npImage.copy()
    imgHeight = image.shape[0]
    imgWidth = image.shape[1]

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    
    edged = cv2.Canny(gray, 50, 80)
    edged = cv2.dilate(edged, None, iterations=1)
    edged = cv2.erode(edged, None, iterations=1)
    
    cnts = cv2.findContours(edged.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = imutils.grab_contours(cnts)
    
    if not cnts or len(cnts) == 0:
        return overlay_img

    try:
        (cnts, _) = contours.sort_contours(cnts)
    except Exception:
        pass

    pixelsPerMetricHeight = realHeight/imgHeight
    pixelsPerMetricWidth = realWidth/imgWidth
    
    try:
        bboxes = extract_bboxes(npImage)
        if len(bboxes) > 0:
            y1, x1, y2, x2 = bboxes[0]
            cv2.rectangle(overlay_img,(x1,y1),(x2, y2),(0,255,0),2)
    except Exception:
        x1, y1 = 10, 20

    for c in cnts:
        if cv2.contourArea(c) < 100:
            continue
        orig = overlay_img.copy()
        box = cv2.minAreaRect(c)
        box = cv2.boxPoints(box)
        box = np.array(box, dtype="int")
        box = perspective.order_points(box)

        (tl, tr, br, bl) = box
        (tltrX, tltrY) = midpoint(tl, tr)
        (blbrX, blbrY) = midpoint(bl, br)
        (tlblX, tlblY) = midpoint(tl, bl)
        (trbrX, trbrY) = midpoint(tr, br)

        cv2.line(orig, (int(tlblX), int(tlblY)), (int(trbrX), int(trbrY)), (255, 0, 0), 1)
        
        top_p = min([(int(tlblX), int(tlblY)), (int(trbrX), int(trbrY))], key=lambda x : x[1])
        bot_p = max([(int(tlblX), int(tlblY)), (int(trbrX), int(trbrY))], key=lambda x : x[1])
        D_ad = ((top_p[1] - bot_p[1]) ** 2 + (top_p[0] - bot_p[0])**2) ** 0.5 + 1e-7
    
        P1 = min(top_p, bot_p, key=lambda x:x[0])
        P2 = max(top_p, bot_p, key=lambda x:x[0])
        
        # ЗАЩИТА №1: Блокировка деления на ноль при вертикальных трещинах
        try:
            slope = (P1[1] - P2[1]) / (P2[0] - P1[0])
        except ZeroDivisionError:
            slope = 0
            
        cat = ''
        angle = 0        
        if slope > 0:
            angle = np.arccos((top_p[0] - bot_p[0])/D_ad) * 180 / math.pi
            cv2.putText(orig, "angle={:.1f}".format(angle), (max(top_p[0]-100, 0), top_p[1] + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
        else:
            angle = np.arccos((bot_p[0] - top_p[0])/D_ad) * 180 / math.pi  
            cv2.putText(orig, "angle={:.1f}".format(angle), (top_p[0], top_p[1] + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
        
        dA = dist.euclidean((tltrX, tltrY), (blbrX, blbrY))
        dB = dist.euclidean((tlblX, tlblY), (trbrX, trbrY))
        length = cv2.arcLength(c, True) / 2. * pixelsPerMetricWidth
        
        M = cv2.moments(c)
        # ЗАЩИТА №2: Блокировка деления на ноль, если площадь маски нулевая
        if M["m00"] == 0:
            continue
        cX = int(M["m10"] / M["m00"])
        cY = int(M["m01"] / M["m00"])
    
        mask_gray = gray.copy()
        mask_gray[mask_gray < 40] = 0
        
        try:
            width = cv2.countNonZero(mask_gray[cY][:])
            right_most_x = np.max(np.nonzero(mask_gray[cY][:]))
            left_most_x = np.min(np.nonzero(mask_gray[cY][:]))
            cv2.line(orig, (int(left_most_x), int(cY)), (int(right_most_x), int(cY)), (255, 0, 0), 1)
            width *= pixelsPerMetricWidth 
        except Exception:
            width = dB * pixelsPerMetricWidth

        dimA = dA * pixelsPerMetricHeight
        dimB = dB * pixelsPerMetricWidth
        
        if angle < angle_th:
            cat += 'H'
            cv2.putText(orig, "L={:.1f}".format(length) + unit, (int(tltrX), int(tltrY) + 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
            cv2.putText(orig, "W={:.1f}".format(width) + unit, (int(tltrX), int(tltrY) + 55), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
        else:
            cat += 'V'
            cv2.putText(orig, "L={:.1f}".format(length) + unit, (int(tltrX), int(tltrY)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
            cv2.putText(orig, "W={:.1f}".format(width) + unit, (int(tltrX), int(tltrY) + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
        
        if slope > 0:
            cat += 'L'
        else:
            cat += 'R'
            
        cv2.putText(orig, "cat="+cat + " Crack percentage={:.2f}".format(confidence.item()), (x1, max(0, y1-5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (36,255,12), 1)
        return orig
    return overlay_img

# --- ФУНКЦИИ ИНФЕРЕНСА МОДЕЛИ ---

def tensor2im(input_image, imtype=np.uint8):
    if not isinstance(input_image, np.ndarray):
        if isinstance(input_image, torch.Tensor):
            image_tensor = input_image.data
        else:
            return input_image
        image_numpy = image_tensor[0].cpu().float().numpy()
        if image_numpy.shape[0] == 1:
            image_numpy = np.tile(image_numpy, (3, 1, 1))
        image_numpy = (np.transpose(image_numpy, (1, 2, 0)) + 1) / 2.0 * 255.0
    else:
        image_numpy = input_image
    return image_numpy.astype(imtype)

def bytes_to_array(b: bytes) -> np.ndarray:
    np_bytes = BytesIO(b)
    return np.load(np_bytes, allow_pickle=True)

def read_image(bytesImg, dim=(256, 256)):
    img_transforms = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])
    img = np.frombuffer(bytesImg, np.uint8)
    img = cv2.imdecode(img, cv2.IMREAD_COLOR)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    
    w, h = dim
    if w > 0 or h > 0:
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_CUBIC)
    
    img = img_transforms(Image.fromarray(img.copy()))   
    return img    

def create_model(opt, cp_path='pretrained_net_G.pth'):
    model = DeepCrackModel(opt)
    checkpoint = torch.load(cp_path, map_location=torch.device('cpu')) # Принудительно на CPU для ноута
    if hasattr(model.netG, 'module'):
        model.netG.module.load_state_dict(checkpoint, strict=False)
    else:
        model.netG.load_state_dict(checkpoint, strict=False)
    model.eval()
    return model

def overlay(image: np.ndarray, mask: np.ndarray, color = (255, 0, 0), alpha: float = 0.5, resize = (256, 256)) -> np.ndarray:
    color = np.asarray(color).reshape(1, 1, 3)
    colored_mask = np.expand_dims(mask, 0).repeat(3, axis=2)
    masked = np.ma.MaskedArray(image, mask=colored_mask, fill_value=color)
    image_overlay = masked.filled()
    
    if resize is not None:
        image = cv2.resize(image, resize)
        image_overlay = cv2.resize(image_overlay, resize)
    
    image_combined = cv2.addWeighted(image, 1 - alpha, image_overlay, alpha, 0)
    return image_combined

def inference(model, bytesImg, dim, unit):
    image = read_image(bytesImg)
    image = image.unsqueeze(0)
    model.set_input({'image': image, 'label': torch.zeros_like(image), 'A_paths':''}) 
    model.test()
    visuals = model.get_current_visuals()
    confidence = visuals['fused'].max()

    for key in visuals.keys():
        visuals[key] = tensor2im(visuals[key])
        
    fused = Image.fromarray(visuals['fused'])
    fused = np.array(fused, dtype='uint8')
    realHeight=dim[1]
    realWidth=dim[0]

    mask = cv2.cvtColor(fused, cv2.COLOR_BGR2GRAY)
    mask[mask < 90] = 0
    mask[mask >= 90] = 255
    cnts = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    overlay_img = overlay(tensor2im(image), mask, alpha=0)
    cv2.drawContours(image=overlay_img, contours=cnts[0], contourIdx=-1, color=(0, 255, 0), thickness=1, lineType=cv2.LINE_AA)
    
    # Прямой вызов getContours из этого же файла
    contour_img = getContours(fused, overlay_img, realHeight, realWidth, unit, confidence)

    return contour_img if contour_img is not None else overlay_img, visuals
    