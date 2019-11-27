#    Copyright 2019 Google LLC
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        https://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

"""A demo to run the detector in a Pygame camera stream."""
import argparse
import collections
from collections import deque
import io
import numpy as np
import os
import pygame
import pygame.camera
from pygame.locals import *
import re
import tflite_runtime.interpreter as tflite
import time

EDGETPU_SHARED_LIB = 'libedgetpu.so.1'
Object = collections.namedtuple('Object', ['id', 'score', 'bbox'])

def load_labels(path):
    p = re.compile(r'\s*(\d+)(.+)')
    with open(path, 'r', encoding='utf-8') as f:
       lines = (p.match(line).groups() for line in f.readlines())
       return {int(num): text.strip() for num, text in lines}

def make_interpreter(model_file):
    model_file, *device = model_file.split('@')
    return tflite.Interpreter(
      model_path=model_file,
      experimental_delegates=[
          tflite.load_delegate(EDGETPU_SHARED_LIB,
                               {'device': device[0]} if device else {})
      ])

def input_size(interpreter):
    """Returns input image size as (width, height, channels) 3-tuple."""
    _, height, width, channels = interpreter.get_input_details()[0]['shape']
    return width, height, channels

def input_tensor(interpreter):
    """Returns input tensor view as numpy array of shape (height, width, 3)."""
    tensor_index = interpreter.get_input_details()[0]['index']
    return interpreter.tensor(tensor_index)()[0]

def output_tensor(interpreter, i):
    """Returns output tensor view."""
    tensor = interpreter.tensor(interpreter.get_output_details()[i]['index'])()
    return np.squeeze(tensor)

def set_interpreter(interpreter, data):
    input_tensor(interpreter)[:,:] = np.reshape(data, (input_size(interpreter)))
    interpreter.invoke()

class BBox(collections.namedtuple('BBox', ['xmin', 'ymin', 'xmax', 'ymax'])):
    """Bounding box.
    Represents a rectangle which sides are either vertical or horizontal, parallel
    to the x or y axis.
    """
    __slots__ = ()

def get_output(interpreter, score_threshold, image_scale=1.0):
    """Returns list of detected objects."""
    boxes = output_tensor(interpreter, 0)
    class_ids = output_tensor(interpreter, 1)
    scores = output_tensor(interpreter, 2)
    count = int(output_tensor(interpreter, 3))

    def make(i):
        ymin, xmin, ymax, xmax = boxes[i]
        return Object(
            id=int(class_ids[i]),
            score=scores[i],
            bbox=BBox(xmin=np.maximum(0.0, xmin),
                      ymin=np.maximum(0.0, ymin),
                      xmax=np.minimum(1.0, xmax),
                      ymax=np.minimum(1.0, ymax)))

    return [make(i) for i in range(count) if scores[i] >= score_threshold]


def main():
    cam_w, cam_h = 640, 480
    default_model_dir = "../all_models"
    default_model = 'mobilenet_ssd_v2_coco_quant_postprocess_edgetpu.tflite'
    default_labels = 'coco_labels.txt'
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', help='.tflite model path',
                        default=os.path.join(default_model_dir,default_model))
    parser.add_argument('--labels', help='label file path',
                        default=os.path.join(default_model_dir, default_labels))
    parser.add_argument('--top_k', type=int, default=5,
                        help='number of classes with highest score to display')
    parser.add_argument('--threshold', type=float, default=0.5,
                        help='class score threshold')
    args = parser.parse_args()

    with open(args.labels, 'r') as f:
        pairs = (l.strip().split(maxsplit=1) for l in f.readlines())
        labels = dict((int(k), v) for k, v in pairs)

    print("Loading %s with %s labels."%(args.model, args.labels))

    interpreter = make_interpreter(args.model)
    interpreter.allocate_tensors()
    labels = load_labels(args.labels)

    pygame.init()
    pygame.font.init()
    font = pygame.font.SysFont("Arial", 20)

    pygame.camera.init()
    camlist = pygame.camera.list_cameras()

    w, h, _ = input_size(interpreter)
    camera = pygame.camera.Camera(camlist[0], (cam_w, cam_h))
    display = pygame.display.set_mode((cam_w, cam_h), 0)

    red = pygame.Color(255, 0, 0)

    camera.start()
    try:
        last_time = time.monotonic()
        while True:
            mysurface = camera.get_image()
            imagen = pygame.transform.scale(mysurface, (w, h))
            input = np.frombuffer(imagen.get_buffer(), dtype=np.uint8)
            start_time = time.monotonic()
            set_interpreter(interpreter, input)
            results = get_output(interpreter, score_threshold=args.threshold)
            stop_time = time.monotonic()
            inference_ms = (stop_time - start_time)*1000.0
            fps_ms = 1.0 / (stop_time - last_time)
            last_time = stop_time
            annotate_text = "Inference: %5.2fms FPS: %3.1f" % (inference_ms, fps_ms)
            for result in results:
               x0, y0, x1, y1 = list(result.bbox)
               rect = pygame.Rect(x0 * cam_w, y0 * cam_h, (x1 - x0) * cam_w, (y1 - y0) * cam_h)
               pygame.draw.rect(mysurface, red, rect, 1)
               label = "%.0f%% %s" % (100*result.score, labels.get(result.id, result.id))
               text = font.render(label, True, red)
               mysurface.blit(text, (x0 * cam_w , y0 * cam_h))
            text = font.render(annotate_text, True, red)
            mysurface.blit(text, (0, 0))
            display.blit(mysurface, (0, 0))
            pygame.display.flip()
    finally:
        camera.stop()


if __name__ == '__main__':
    main()
