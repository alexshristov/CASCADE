from cascade.extraction.Extraction import Extraction
from typing import List, Dict
from cascade.utils.Utils import load_json_from_path
import os


class JsonExtraction(Extraction):
    def __init__(self):
        super().__init__()
    """
    This is a basic extraction module
    """
    def extract(self, input_path, output_path) -> List[Dict[str, any]]:
        """
        checks if the file specified in the output path already exists
        and if yes loads it and returns a list of dictionaries.

        If not it checks if the input path points to a json file and extracts this.

        if neither it returns an empty list.

        :param input_path:
        :param output_path:
        :return:
        """
        # Check if the output file exists
        if os.path.exists(output_path):
            if "extracted.json" in os.listdir(output_path):
                return load_json_from_path(os.path.join(output_path, "extracted.json"))
        else:
            os.makedirs(output_path)

        # Check if the input path points to a JSON file
        if input_path.endswith(".json") or input_path.endswith(".jsonl"):
            return load_json_from_path(input_path)
            # # the standard human eval file has this ending and is technically not a legit json format
            # # but each line is a json entry (not comma seperated like a json list would be)
            # if ending == ".jsonl":
            #     data = []
            #     with open(input_path, 'r') as file:
            #         for line in file:
            #             data.append(json.loads(line))
            #     return data

        # Return an empty list if neither condition is met
        return []
