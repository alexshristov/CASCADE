import json
import subprocess

from cascade.analysis.executor.AnalysisExecutor import AnalysisExecutor
from cascade.utils.DockerizedWrapper import DockerizedWrapper
from cascade.utils.Metrics import metric_context, time_block

import re
import os
import tempfile
import shutil


class JavaExecutor(AnalysisExecutor):

    def __init__(self, debug=False, builder=None):
        super().__init__()
        self.debug = debug
        self.builder = builder

    def execute(self, code: str, tests: str, context: dict, input_path, output_path):
        """
        This Method executes given test cases and code. For this it ...

        :param code: The key for an entry in the context dictionary that is the code block to be tested. Should be "code"
        :param tests: The key for an entry in the context dictionary that is the test file to be run. E.g. "tests" or if present "new_tests"
        :param context: The context dictionary that describes the function to be tested. As created by the extractor class. Has to contain at least the keys "test_file_path", "code_file_path", "test_package", "id" as well as the keys passed in the oce and tests parameters.
        :param input_path: The path to the root of the project under test.
        :param output_path: The path to the output folder. This is where the results of the analysis as well as some intermediate files and logging data will be stored.
        :return: a 2-tuple of
            first a three-tuple of lists of strings,
                the first list contains the names of the tests that passed,
                the second list contains the names of the tests that failed,
                the third list contains the names of the tests that errored
            second a string containing any (compilation) errors that happened during execution or 'None' if none occurred
        """

        fields = {
            "sample_id": context.get("id"),
            "method_name": context.get("signature", {}).get("name"),
            "component": self.__class__.__name__,
        }
        with metric_context(**fields):
            with tempfile.TemporaryDirectory() as temp_dir:
                try:
                    with time_block("java_project_copy"):
                        shutil.copytree(input_path, temp_dir, dirs_exist_ok=True)

                except Exception as e:
                    print("could not copy root path")
                    print(e)

                entry = os.path.join(temp_dir, "entry.json")
                with open(entry, "w") as json_entry:
                    json.dump(context, json_entry)

                my_path = os.path.dirname(__file__)
                with time_block("java_modify", code_key=code, tests_key=tests):
                    p = subprocess.run(
                        ["java", "-jar", os.path.join(my_path, "..", "..", "resources", "tools", "JavaExtractor.jar"),
                         "mod",  #modification mode
                         temp_dir,
                         entry,
                         code,
                         tests],
                        capture_output=True,
                        text=True
                    )

                os.remove(entry)

                with open(os.path.join(output_path, "log.txt"), "a") as file:
                    file.write(str(context["id"]) + "\n")
                    file.write(p.stdout + "\n")
                    file.write(p.stderr + "\n")

                if p.stderr:
                    if self.debug:
                        print(p.stdout)
                        print(p.stderr)
                    return ([],[],[]), None

                if self.debug:
                    print(p.stdout)

                dock_ex = DockerizedWrapper(debug=self.debug)

                test_command = (self.builder.test_pattern.replace('%t', "THIS_IS_A_UNIQUE_NAME_Test"))
                dock_context = {
                    "image" : self.builder.image,
                    "directory" : temp_dir,
                    "command" : f"ls; cat -n {context['code_file_path']}; cat -n {context['test_file_path']};"
                                f"{test_command}",
                    "eval_command" : "cat out",
                    "eval_function" : self.builder.eval_function
                }

                with time_block("java_test_execution", code_key=code, tests_key=tests):
                    result = dock_ex.execute(dock_context, output_path)

        return result


    def set_up(self, data, input_path, output_path):
        """
        Set up the environment for the execution of the Java tests.
        This is useful to use if several tests from the same project will be executed, to save time.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                with time_block("java_executor_setup_copy"):
                    shutil.copytree(input_path, temp_dir, dirs_exist_ok=True)

            except Exception as e:
                print("could not copy root path")
                print(e)

            if self.builder:
                with time_block("builder_setup", builder=self.builder.__class__.__name__):
                    return self.builder.set_up(temp_dir, input_path, input_path)
        return False


    def tear_down(self, data):
        context = data[0]

        if self.builder:
            with time_block("builder_teardown", builder=self.builder.__class__.__name__):
                self.builder.tear_down(context)
