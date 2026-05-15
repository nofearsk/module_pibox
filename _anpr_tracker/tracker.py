import numpy as np
import cv2
import re
import time
import requests
import base64
import logging
import json
import threading
import asyncio
import websockets
from typing import Dict, List
from difflib import SequenceMatcher
from paddleocr import PaddleOCR
from concurrent.futures import ThreadPoolExecutor

class Capture:
    thread = None
    running = True
    capture = None
    frame = None
    error_code = None

    def __init__(self, path):
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.stream, args=(path,))
        self.thread.daemon = True
        self.thread.start()

    def release(self):
        self.running = False
        if self.capture != None:
            self.capture.release()

    def read(self):
        return self.frame

    def clear(self):
        self.frame = None

    def stream(self, path):
        while self.running:
            self.capture = cv2.VideoCapture(path, cv2.CAP_FFMPEG)

            if not self.capture.isOpened():
                self.error_code = 100 #print("Unable to open the path, retrying...")
                time.sleep(5)
                continue

            while self.running:
                _, self.frame = self.capture.read()

                if not _:
                    self.error_code = 101 #print("Error: Couldn't read frame")
                    self.frame = None
                    self.capture.release()
                    time.sleep(5)
                    break

                if self.error_code != None:
                    self.error_code = None

class Tracker:
    must_restart = None

    update_status_rate: int = 5
    update_status_code: int = 200
    update_status_name: str = None

    update_image: bool = False
    update_image_rate: int = 5
    update_image_frame: np.ndarray = None

    debug: bool = False
    tracker_id: int = None
    tracker_type: str = None

    api_url: str = None
    api_header: Dict[str, str] = {}

    guard_api_url: str = None
    guard_api_header: Dict[str, str] = {}

    keeper_api_url: str = None
    keeper_api_header: Dict[str, str] = {}

    gate_url: str = None
    stream_url: str = None

    max_valid_detect_seconds: int = 1
    max_same_valid_detect_seconds: int = 5

    max_first_detect_seconds: int = None
    max_last_detect_seconds: int = None

    max_distance_threshold: int = 10

    min_read_score: float = 0.8
    min_ratio_score: float = 0.85

    detect_region: List[int]
    draw_detect_region: bool = False

    request_timeout: int = 10
    send_response_max_attempt: int = 10

    ocr: PaddleOCR
    cap: Capture
    plates_image: Dict[str, np.ndarray] = {}
    plates_score: Dict[str, float] = {}
    plates_counter: Dict[str, int] = {}

    first_detect_time: float = None
    last_detect_time: float = None

    parked_vehicles: List[str] = []
    whitelist_vehicles: List[str] = []
    blacklist_vehicles: List[str] = []

    logger: logging.Logger = None

    def __init__(self, logger: logging.Logger, stream_url, must_restart, parked_vehicle: List[str], whitelist_vehicle: List[str], blacklist_vehicle: List[str]) -> None:
        self.logger = logger
        self.stream_url = stream_url
        self.must_restart = must_restart
        self.parked_vehicles = parked_vehicle
        self.whitelist_vehicles = whitelist_vehicle
        self.blacklist_vehicles = blacklist_vehicle

        self.logger.info(f"streaming: start {stream_url}")
        self.ocr = PaddleOCR(lang="en", use_angle_cls=True, show_log=False)

    def update_status_handler(self):
        self.logger.info("update_status: starting")
        while self.must_restart.value == False:
            data = {
                "id": self.tracker_id,
                "code": self.update_status_code
            }
            try:
                response = requests.post(f"{self.api_url}/tracker", json=data, timeout=self.request_timeout, headers=self.api_header)
                if response.status_code != 200:
                    continue

                data = response.json()
                self.logger.debug(f"update_status: {data}")

                self.tracker_id = data["id"]
                self.tracker_type = data["type"]
                self.guard_api_url = data["guardApiUrl"]
                self.guard_api_header = { "Authorization" : f"Bearer {data['guardApiToken']}" }
                self.keeper_api_url = data["keeperApiUrl"]
                self.keeper_api_header = { "Authorization" : f"Bearer {data['keeperApiToken']}" }
                self.stream_url = data["streamUrl"]
                self.gate_url = data["gateUrl"]
                self.max_same_valid_detect_seconds = data["maxSameValidDetectSeconds"]
                self.max_valid_detect_seconds = data["maxValidDetectSeconds"]
                self.max_first_detect_seconds = data["maxFirstDetectSeconds"]
                self.max_last_detect_seconds = data["maxLastDetectSeconds"]
                self.max_distance_threshold = data["maxDistanceThreshold"]
                self.min_read_score = data["minReadScore"]
                self.min_ratio_score = data["minRatioScore"]
                self.detect_region = [int(x) for x in data["detectRegion"].split()]
                self.draw_detect_region = data["drawDetectRegion"]
                self.update_image = data["updateImage"]
                self.update_image_rate = data["updateImageRate"]
            except requests.exceptions.RequestException as error:
                self.logger.error("*** update_status: an exception occurred: %s", type(error).__name__)
            except Exception as error:
                self.logger.error("*** update_status: an exception occurred:", type(error).__name__, "–", error)

            time.sleep(5)
        self.logger.info("update_status: stop")

    def update_image_handler(self):
        self.logger.info("update_image: starting")
        while self.must_restart.value == False:
            if self.update_image == True:
                if self.update_image_frame is None:
                    self.logger.debug("update_image: frame is None")
                    time.sleep(self.update_image_rate)
                    continue

                self.logger.debug("*** update_image: start")
                encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 50]
                _, image = cv2.imencode(".jpg", self.update_image_frame, encode_param)
                data = {
                    "id": self.tracker_id,
                    "image": base64.b64encode(image).decode("utf-8")
                }
                try:
                    rs = requests.post(f"{self.api_url}/tracker/image", json=data, timeout=self.request_timeout, headers=self.api_header)
                    if rs.status_code == 200:
                        None
                except requests.exceptions.RequestException as error:
                    self.logger.error("*** update_image: an exception occurred: %s", type(error).__name__)
                except Exception as error:
                    self.logger.error("*** update_image: an exception occurred:", type(error).__name__, "–", error)

            time.sleep(self.update_image_rate)
        self.logger.info("update_image: stop")

    def open_gate(self) -> bool:
        self.logger.info(f"*** open_gate: {self.gate_url}")
        if self.gate_url is None or len(self.gate_url.strip()) == 0:
            self.logger.debug("*** open_gate: no gate")
            return True

        try:
            res = requests.get(self.gate_url, timeout=self.request_timeout)
            if res.status_code == 200:
                self.logger.info(f"*** open_gate: success")
                return True
            else:
                self.logger.error(f"*** open_gate: error {res.status_code}")
        except requests.exceptions.RequestException as error:
            self.update_status_code = 300
            self.logger.error("*** open_gate: an exception occurred: %s", type(error).__name__)
        except Exception as error:
            self.logger.error("*** open_gate: an exception occurred:", type(error).__name__, "–", error)
        finally:
            return False

    def send_result(self, target: str, headers: Dict[str, str], number: str, score: float, image: np.ndarray):
        self.logger.info(f"*** send_result: {target} {number}")

        if target is None:
            self.logger.info("*** send_result: target is None")
            return

        data = {
            "trackerId": self.tracker_id,
            "number": number,
            "score": score,
        }
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 50]
        _, plate_image = cv2.imencode(".jpg", image, encode_param)
        data["image"] = base64.b64encode(plate_image).decode("utf-8")

        for attempt in range(self.send_response_max_attempt):
            try:
                self.logger.info(f"*** send_result: attempt #{attempt + 1} {target} {number}")
                response = requests.post(target, json=data, timeout=self.request_timeout, headers=headers)
                if response.status_code == 200:
                    self.logger.info(f"*** send_result: done {target} {number}")
                    return
                else:
                    self.logger.info("*** send_result: status {response.status_code}")
                    time.sleep(1)
            except requests.exceptions.RequestException as error:
                self.update_status_code = 400
                self.logger.error("*** send_result: an exception occurred: %s", type(error).__name__)
            except Exception as error:
                self.logger.error("*** open_gate: an exception occurred:", type(error).__name__, "–", error)

        self.logger.error(f"*** send_result: max attempt reached, failed to send {target} {number}")

    def send_whitelist_api(self, number: str, score: float, image: np.ndarray) -> None:
        if self.api_url is not None and self.api_url.strip():
            self.send_result(target=f"{self.api_url}/trace", headers=self.api_header, number=number, score=score, image=image)

    def send_visitor_api(self, number: str, score: float, image: np.ndarray) -> None:
        ##self.send_result(target=f"{self.api_url}/trace", headers=self.api_header, number=number, score=score, image=image)

        if self.keeper_api_url is not None and self.keeper_api_url.strip():
            self.send_result(target=self.keeper_api_url, headers=self.keeper_api_header, number=number, score=score, image=image)

        if self.guard_api_url is not None and self.guard_api_url.strip():
            quick_patch_image = cv2.resize(image, (800, 600))
            self.send_result(target=self.guard_api_url, headers=self.guard_api_header, number=number, score=score, image=quick_patch_image)

    def validate(self, number_to_check: str):
        for number in self.blacklist_vehicles:
            match_ratio = SequenceMatcher(None, number, number_to_check).ratio()
            if(match_ratio > self.min_ratio_score):
                self.logger.info(f"{number} {match_ratio} found blacklist")
                return False, number

        best_match_number = None
        highest_ratio = 0.0

        for number in self.whitelist_vehicles:
            match_ratio = SequenceMatcher(None, number, number_to_check).ratio()
            if match_ratio > self.min_ratio_score and match_ratio > highest_ratio:
                highest_ratio = match_ratio
                best_match_number = number

        if best_match_number is not None:
            self.logger.info(f"{best_match_number} {highest_ratio} found whitelist")
            return True, best_match_number

        best_match_number = None
        highest_ratio = 0.0

        if self.tracker_type == 'EXIT':
            for number in self.parked_vehicles:
                match_ratio = SequenceMatcher(None, number, number_to_check).ratio()
                if match_ratio > self.min_ratio_score and match_ratio > highest_ratio:
                    highest_ratio = match_ratio
                    best_match_number = number

            if best_match_number is not None:
                self.logger.info(f"{best_match_number} {highest_ratio} found parked")
                return True, best_match_number

        return False, None

    def reset_detection(self):
        self.plates_image = {}
        self.plates_score = {}
        self.plates_counter = {}

        self.first_detect_time = None
        self.last_detect_time = None

    def run(self):
        self.logger.info("Starting")
        update_status_thread = threading.Thread(target=self.update_status_handler)
        update_status_thread.start()

        update_image_thread = threading.Thread(target=self.update_image_handler)
        update_image_thread.start()

        while self.must_restart.value == False:
            self.cap = Capture(self.stream_url)
            self._run_internal()
            self.cap.release()

        update_image_thread.join()
        update_status_thread.join()

        self.logger.info("Stopping")

    def calculate_boxes_distance(self, box1, box2):
        # Calculate the distance between the centers of two bounding boxes
        x1_center = (box1[0][0] + box1[2][0]) / 2
        y1_center = (box1[0][1] + box1[2][1]) / 2
        x2_center = (box2[0][0] + box2[2][0]) / 2
        y2_center = (box2[0][1] + box2[2][1]) / 2
        return ((x2_center - x1_center)**2 + (y2_center - y1_center)**2)**0.5

    def combine_boxes(self, box1, box2):
        # Combine two bounding boxes into one that encompasses both
        x_coords = [point[0] for point in box1 + box2]
        y_coords = [point[1] for point in box1 + box2]
        return [[min(x_coords), min(y_coords)], [max(x_coords), min(y_coords)], [max(x_coords), max(y_coords)], [min(x_coords), max(y_coords)]]

    def join_text_by_distance(self, data, distance_threshold):
        joined_data = []
        current_text = ""
        current_box = None
        current_scores = []

        for bbox, (text, score) in data:
            if current_box is not None and self.calculate_boxes_distance(current_box, bbox) <= distance_threshold:
                current_text += " " + text
                current_box = self.combine_boxes(current_box, bbox)
                current_scores.append(score)
            else:
                if current_text:
                    average_score = sum(current_scores) / len(current_scores)
                    joined_data.append((current_box, (current_text, average_score)))
                current_text = text
                current_box = bbox
                current_scores = [score]

        if current_text:
            average_score = sum(current_scores) / len(current_scores)
            joined_data.append((current_box, (current_text, average_score)))

        return joined_data

    def _run_internal(self):
        if self.debug:
            cv2.namedWindow(f"Graybox {self.tracker_id}", cv2.WINDOW_NORMAL)
            cv2.namedWindow(f"Tracker {self.tracker_id}", cv2.WINDOW_NORMAL)

        """ Add mechanism to expired this thing (it wont stay forever) """
        last_best_plate_number: str = None
        last_valid_plate_number: str = None
        last_valid_detect_time: float = None
        last_same_valid_detect_time: float = None
        with ThreadPoolExecutor(max_workers=16) as executor:
            while self.must_restart.value == False:
                frame = self.cap.read()
                if self.cap.error_code != None:
                    self.update_status_code = self.cap.error_code
                    time.sleep(5)
                    continue
                if frame is None: continue
                self.cap.clear()

                # We can assume the camera error (1xx) are gone here
                if self.update_status_code >= 100 or self.update_status_code <= 199:
                    self.update_status_code = 200

                """
                Deblur algorithm
                https://medium.com/machine-learning-world/deblur-photos-using-generic-pix2pix-6f8774f9701e
                """
                image = frame[self.detect_region[1] : self.detect_region[3], self.detect_region[0] : self.detect_region[2]]
                gray_image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)

                #sharpen_kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
                #gray_image = cv2.filter2D(gray_image, -1, sharpen_kernel)

                if self.draw_detect_region == True:
                    frame = cv2.rectangle(frame, (self.detect_region[0], self.detect_region[1]), (self.detect_region[2], self.detect_region[3]), (100, 200, 100), 4)

                if self.update_image == True:
                    self.update_image_frame = frame.copy()

                if last_valid_detect_time is None:
                    results = self.ocr.ocr(gray_image)
                    if len(results) > 0 and results[0] is not None:
                        result = self.join_text_by_distance(results[0], self.max_distance_threshold)

                        for res in result:
                            res_boxes = res[0]
                            res_texts = res[1]
                            self.logger.debug(f"ocr: {res_boxes} {res_texts}")

                            plate_score = res_texts[1]
                            if plate_score < self.min_read_score:
                                continue

                            plate_number = "".join(res_texts[0]).replace(" ", "")
                            is_valid = re.search("^[A-Z]{1,3}[1-9]{1}[0-9]{0,3}[A-Z]?$", plate_number)
                            if is_valid is None:
                                continue

                            if self.draw_detect_region == True:
                                x1 = int(res_boxes[0][0]) + self.detect_region[0]
                                x2 = int(res_boxes[0][1]) + self.detect_region[1]
                                y1 = int(res_boxes[2][0]) + self.detect_region[0]
                                y2 = int(res_boxes[2][1]) + self.detect_region[1]
                                cv2.rectangle(frame, (x1, x2), (y1, y2), (200, 100, 100), 4)

                            self.logger.info(f"read {plate_number} {plate_score}")

                            if self.first_detect_time is None:
                                self.first_detect_time = time.time()

                            if last_valid_plate_number == plate_number:
                                self.logger.info(f"last_valid_same: {last_valid_plate_number}")
                                self.reset_detection()
                                continue

                            _is_valid, _valid_plate = self.validate(plate_number)
                            if _is_valid:
                                if last_valid_plate_number != _valid_plate:
                                    self.logger.info(f"last_valid_not_same: {last_valid_plate_number}")
                                    last_valid_plate_number = _valid_plate
                                    last_valid_detect_time = time.time()
                                    last_same_valid_detect_time = last_valid_detect_time
                                    executor.submit(self.open_gate)
                                    executor.submit(self.send_whitelist_api, _valid_plate, plate_score, frame.copy())
                                else:
                                    self.logger.info(f"last_valid_same: {last_valid_plate_number} {plate_number}")
                                    last_valid_detect_time = time.time()
                                    last_same_valid_detect_time = last_valid_detect_time

                                self.reset_detection()
                                continue

                            """ Black listed """
                            if _valid_plate is not None:
                                self.logger.info(f"valid_blacklist: {_valid_plate}")
                                last_valid_plate_number = _valid_plate
                                last_valid_detect_time = time.time()
                                last_same_valid_detect_time = last_valid_detect_time
                                continue

                            if plate_number not in self.plates_counter:
                                self.plates_counter[plate_number] = 1
                            else:
                                self.plates_counter[plate_number] += 1

                            if plate_number not in self.plates_score or self.plates_score[plate_number] < plate_score:
                                self.plates_score[plate_number] = plate_score
                                self.plates_image[plate_number] = frame

                            self.last_detect_time = time.time()

                if(self.max_valid_detect_seconds is not None and
                   last_valid_detect_time is not None and
                   time.time() - last_valid_detect_time > self.max_valid_detect_seconds):
                    self.logger.info("clear: valid")
                    last_best_plate_number = None
                    last_valid_detect_time = None
                    self.reset_detection()

                if(self.max_same_valid_detect_seconds is not None and
                   last_same_valid_detect_time is not None and
                   time.time() - last_same_valid_detect_time > self.max_same_valid_detect_seconds):
                    self.logger.info("clear: same_valid")
                    last_same_valid_detect_time = None
                    last_valid_plate_number = None
                    self.reset_detection()

                if (self.max_first_detect_seconds is not None and
                    self.first_detect_time is not None and
                    time.time() - self.first_detect_time > self.max_first_detect_seconds):

                    if len(self.plates_counter) < 1:
                        self.reset_detection()
                        continue

                    best_plate_counter = 0
                    best_plate_number = None
                    for plate_number in self.plates_counter:
                        counter = self.plates_counter[plate_number] + len(plate_number)
                        if best_plate_counter < counter:
                            best_plate_number = plate_number
                            best_plate_counter = counter

                    if last_best_plate_number != best_plate_number:
                        self.logger.info(f"send_first_detect_time: {last_best_plate_number} {best_plate_number} {best_plate_counter}")
                        last_valid_detect_time = time.time()
                        last_best_plate_number = best_plate_number
                        executor.submit(self.send_visitor_api, best_plate_number, self.plates_score[best_plate_number], self.plates_image[best_plate_number].copy())
                    else:
                        self.logger.info(f"skip {best_plate_number}")

                    self.reset_detection()

                if (self.max_last_detect_seconds is not None and
                    self.last_detect_time is not None and
                    time.time() - self.last_detect_time > self.max_last_detect_seconds):
                    if len(self.plates_counter) < 1:
                        self.reset_detection()
                        continue

                    best_plate_counter = 0
                    best_plate_number = None
                    for plate_number in self.plates_counter:
                        counter = self.plates_counter[plate_number] + len(plate_number)
                        if best_plate_counter < counter:
                            best_plate_number = plate_number
                            best_plate_counter = counter

                    self.logger.info(f"send_last_detect_time: {best_plate_number} {best_plate_counter}")
                    executor.submit(self.send_visitor_api, best_plate_number, self.plates_score[best_plate_number], self.plates_image[best_plate_number].copy())

                    self.reset_detection()

                if self.debug:
                    cv2.imshow(f"Graybox {self.tracker_id}", gray_image)
                    cv2.imshow(f"Tracker {self.tracker_id}", frame)
                    if cv2.waitKey(1) & 0xFF == ord(' '):
                        break

            self.logger.info("_run_internal stopping")