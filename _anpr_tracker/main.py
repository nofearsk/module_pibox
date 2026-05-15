import os
import time
import tracker
import requests
import logging
import multiprocessing
from typing import List
from argparse import ArgumentParser

def tracker_handle(data, api_url, api_token,parked_vehicle, whitelist_vehicle, blacklist_vehicle, must_restart, debug=False):
    log_format = "[%(asctime)s] [{}] [%(levelname)8s]: %(message)s".format(str(os.getpid()).rjust(6, ' '))
    log_formatter  = logging.Formatter(log_format)
    log_stream_handler = logging.StreamHandler()
    log_stream_handler.setFormatter(log_formatter)

    logger = logging.getLogger("tracker_handler")
    logger.propagate = False
    logger.addHandler(log_stream_handler)

    if debug == True:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    logger.info(f"Tracker {data['name']}#{data['id']} started with PID {os.getpid()}")

    while True:
        try:
            t = tracker.Tracker(logger, data["streamUrl"], must_restart, parked_vehicle, whitelist_vehicle, blacklist_vehicle)
            t.debug = debug
            t.tracker_id = data["id"]
            t.api_url = api_url
            t.api_header = { "Authorization" : f"Bearer {api_token}" }
            t.guard_api_url = data["guardApiUrl"]
            t.guard_api_header = { "Authorization" : f"Bearer {data['guardApiToken']}" }
            t.keeper_api_url = data["keeperApiUrl"]
            t.keeper_api_header = { "Authorization" : f"Bearer {data['keeperApiToken']}" }
            t.stream_url = data["streamUrl"]
            t.gate_url = data["gateUrl"]
            t.max_valid_detect_seconds = data["maxValidDetectSeconds"]
            t.max_first_detect_seconds = data["maxFirstDetectSeconds"]
            t.max_last_detect_seconds = data["maxLastDetectSeconds"]
            t.min_read_score = data["minReadScore"]
            t.min_ratio_score = data["minRatioScore"]
            t.detect_region = [int(x) for x in data["detectRegion"].split()]
            t.draw_detect_region = data["drawDetectRegion"]

            t.update_image = data["updateImage"]
            t.update_image_rate = data["updateImageRate"]

            t.run()
        except Exception as error:
            logger.error("*** tracker_handle: an exception occurred: %s", type(error).__name__)
        logger.error("*** tracker_handle: restart")
        time.sleep(5)

def update_vehicles_loop(api_url, api_headers, parked_vehicles, whitelist_vehicles, blacklist_vehicles, parked_vehicles_lock, whitelist_vehicles_lock, blacklist_vehicles_lock, refresh_rate, must_restart, must_restart_lock, debug=False):
    log_format = "[%(asctime)s] [{}] [%(levelname)8s]: %(message)s".format("1".rjust(6, ' '))
    log_formatter  = logging.Formatter(log_format)
    log_stream_handler = logging.StreamHandler()
    log_stream_handler.setFormatter(log_formatter)

    logger = logging.getLogger("tracker_handler")
    logger.propagate = False
    logger.addHandler(log_stream_handler)
    logger.info("Start updater")
    logger.debug(whitelist_vehicles)
    logger.debug(blacklist_vehicles)

    if debug == True:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    while True:
        try:
            response = requests.get(f"{api_url}/updates", headers=api_headers, timeout=60)
            if response.status_code == 200:
                result = response.json()

                if must_restart.value == False and result["mustRestart"]:
                    logger.info("MustRestart is True")
                    with must_restart_lock:
                        must_restart.value = result["mustRestart"]

                with parked_vehicles_lock:
                    parked_vehicles[:] = []
                    for number in result["parked"]:
                        parked_vehicles.append(number)

                with whitelist_vehicles_lock:
                    whitelist_vehicles[:] = []
                    for number in result["whitelist"]:
                        whitelist_vehicles.append(number)

                with blacklist_vehicles_lock:
                    blacklist_vehicles[:] = []
                    for number in result["blacklist"]:
                        blacklist_vehicles.append(number)

            logger.debug(f"MustRestart {must_restart.value}; Parked {len(result['parked'])}; Whitelist {len(result['whitelist'])}; Blacklist {len(result['blacklist'])};")
        except requests.exceptions.RequestException as error:
            logger.error("*** update_vehicles_loop: an exception occurred: %s", type(error).__name__)
        except Exception as error:
            logger.error("*** update_vehicles_loop: an exception occurred: %s", type(error).__name__)

        time.sleep(refresh_rate)

def main():
    parser = ArgumentParser()
    parser.add_argument("-d", "--debug", action="store_true", default=False, help="Enable debug")
    parser.add_argument("-u", "--api-url", required=True, type=str, help="API URL")
    parser.add_argument("-t", "--api-token", required=True, type=str, help="Authorization code from API Server")
    parser.add_argument("-r", "--refresh-rate", type=int, default=300, help="Refresh rate")
    args = parser.parse_args()

    log_format = "[%(asctime)s] [     0] [%(levelname)8s]: %(message)s"
    log_formatter  = logging.Formatter(log_format)
    log_stream_handler = logging.StreamHandler()
    log_stream_handler.setFormatter(log_formatter)

    logger = logging.getLogger(__name__)
    logger.propagate = False
    logger.addHandler(log_stream_handler)

    if args.debug == True:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    logger.info("Agent started")

    api_headers = {
        "Authorization" : f"Bearer {args.api_token}"
    }

    multiprocessing.set_start_method('spawn', force=True)

    must_restart = multiprocessing.Value("b", False)
    must_restart_lock = multiprocessing.Lock()

    mp_manager = multiprocessing.Manager()
    parked_vehicles = mp_manager.list()
    whitelist_vehicles = mp_manager.list()
    blacklist_vehicles = mp_manager.list()

    parked_vehicles_lock = mp_manager.Lock()
    whitelist_vehicles_lock = mp_manager.Lock()
    blacklist_vehicles_lock = mp_manager.Lock()

    update_process = multiprocessing.Process(target=update_vehicles_loop, args=(args.api_url, api_headers, parked_vehicles, whitelist_vehicles, blacklist_vehicles, parked_vehicles_lock, whitelist_vehicles_lock, blacklist_vehicles_lock, args.refresh_rate, must_restart, must_restart_lock, args.debug,))
    update_process.start()

    """ Wait for update """
    time.sleep(5)
    while True:
        """ CUDA can't work without this """
        tracker_processes = []
        logger.info(f"Get {args.api_url}/trackers")
        response = requests.get(f"{args.api_url}/trackers", headers=api_headers)
        for t in response.json():
            process = multiprocessing.Process(target=tracker_handle, args=(t, args.api_url, args.api_token, parked_vehicles, whitelist_vehicles, blacklist_vehicles, must_restart, args.debug,))
            tracker_processes.append(process)
            process.start()

        for process in tracker_processes:
            process.join()

        with must_restart_lock:
            must_restart.value = False

        logger.info("Restarting...")
        time.sleep(5)

if __name__ == "__main__":
    main()