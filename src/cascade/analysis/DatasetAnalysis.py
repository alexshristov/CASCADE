import os
import re
import shutil
import tempfile
import time
from datetime import datetime
import traceback

from cascade.analysis.Analysis import Analysis
from cascade.analysis.executor.Execution import Execution
from cascade.analysis.executor.ExecutionResults import ExecutionResults
from cascade.extraction.JavaExtraction import JavaExtraction
from cascade.utils.JavaUtils import build_signature

from cascade.generation.Generation import Generation
from cascade.utils.Utils import load_json_from_path, save_dicts_list_to_json
from cascade.utils.Metrics import record_metric_event, time_block

from cascade.utils.DockerizedWrapper import DockerizedWrapper
import xml.etree.ElementTree as ET

class DatasetAnalysis(Analysis):
    def __init__(self, generator: Generation, executor: Execution, regenerate=False, reexecute=False, image="maven" , debug=0, step_size=1, max_repair_tries=3):
        super().__init__(generator, executor)
        self.reexecute = reexecute or regenerate
        self.step_size = step_size
        self.regenerate = regenerate
        self.debug = debug
        self.image = image
        self.output_path = ""
        self.max_repair_tries = max_repair_tries


    def analyze(self, data: list, input_path, output_path):
        """
        This is the main analysis of the dataset.
        It will run the analysis on single methods as they are provided in the dataset.

        :param data: a list with one element. The context dictionary for the method under test.
        :param input_path
        :param output_path:
        """
        def log(header, message):
            with open(os.path.join(output_path , "log.txt"), "a") as f:
                f.write(header + "/n")
                f.write(message)

        output_string = ""
        ana_path = os.path.join(output_path, "analyzed.json")

        # load data for this specific run
        data = load_json_from_path(ana_path)
        # take the one element
        d = data[0]

        with open(os.path.join(output_path , "result.txt"), "w") as f:
            f.write("NoInco; error; ; ; ; ; ; ")

        if d is None:
            return

        method_start = time.perf_counter()
        method_success = True
        try :
            # to avoid name clashes with existing tests we define a unique name for the test class
            test_class_real_name = d["test_file_path"].split("/")[-1].split(".")[0]
            test_class_unique_name = "THIS_IS_A_UNIQUE_NAME_Test"

            current_time = datetime.now().strftime("%H:%M:%S")
            print(f"{current_time}  Starting analysis of function: {d['signature']['name']}")
            print("    Step 1 - New Tests")

            if not "new_tests" in d:
                new_tests, chat_history = self.generator.generate_tests(d, input_path, output_path)

                d["new_tests"] = new_tests
                d["new_tests_history"] = chat_history

                save_dicts_list_to_json([d], ana_path)

                if new_tests == "":
                    log("GENERATION: no test could be generated:", str(chat_history))
                    return

            else:
                print("      new tests already generated")

            print("      Set Up Test-Executor-Docker")
            with time_block("executor_setup", item_count=len(data)):
                self.executor.set_up(data, input_path, output_path)


            print("      execute new tests")

            d["results"] = {}
            d["results"]["(code, new_tests)"] = [[],[],[]]

            d["new_tests"] = d["new_tests"].replace(test_class_real_name, test_class_unique_name)
            d["test_file_path"] = d["test_file_path"].replace(test_class_real_name, test_class_unique_name)
            exec_results: ExecutionResults = self.executor.execute("code", "new_tests", d, input_path, output_path)

            res1 = exec_results.results
            comp_errors = exec_results.comp_errors

            with open(os.path.join(output_path , "log.txt"), "a") as f:
                f.write("Results after step 1\n")
                f.write(str(exec_results))

            d["new_tests"] = d["new_tests"].replace(test_class_unique_name, test_class_real_name)
            d["test_file_path"] = d["test_file_path"].replace(test_class_unique_name, test_class_real_name)
            if comp_errors:
                comp_errors = comp_errors.replace(test_class_unique_name, test_class_real_name)

            evaluated = self.evaluate(res1)

            # this is the compilation error loop.  so far hard coded number for tries.
            current_repair_tries = 0
            d["repair_history"] = []
            for i in range(self.max_repair_tries):
                # repair step
                # if there were actually compiler errors with the tests:
                if evaluated == 0 and comp_errors:
                    current_repair_tries += 1
                    print("      Try to generate repaired tests")
                    with open(os.path.join(output_path , "log.txt"), "a") as f:
                        f.write(f"start repair generation, trial {current_repair_tries}\n")

                    repaired_tests, response_history = self.generator.repair_tests(d, input_path, output_path, comp_errors, 'new_tests')
                    d["repair_history"].append(response_history)

                    old_tests_key = "tests_pre_repairstep_" + str(i + 1)
                    d[old_tests_key] = d["new_tests"]
                    d["new_tests"] = repaired_tests


                    print("      execute repaired tests")

                    d["new_tests"] = d["new_tests"].replace(test_class_real_name, test_class_unique_name)
                    d["test_file_path"] = d["test_file_path"].replace(test_class_real_name, test_class_unique_name)

                    with open(os.path.join(output_path , "log.txt"), "a") as f:
                        f.write(f"execute repaired tests (trial {current_repair_tries})\n")

                    exec_results: ExecutionResults = self.executor.execute("code", "new_tests", d, input_path, output_path)
                    res1 = exec_results.results
                    comp_errors = exec_results.comp_errors

                    d["new_tests"] = d["new_tests"].replace(test_class_unique_name, test_class_real_name)
                    d["test_file_path"] = d["test_file_path"].replace(test_class_unique_name, test_class_real_name)
                    if comp_errors:
                        comp_errors = comp_errors.replace(test_class_unique_name, test_class_real_name)

                    with open(os.path.join(output_path, "log.txt"), "a") as f:
                        f.write(f"Results after step 1 Repairstep: {current_repair_tries}\n")
                        f.write(str(exec_results))

                    evaluated = self.evaluate(res1)
                    save_dicts_list_to_json([d], ana_path)


            amount_res = exec_results.results_numbers
            d["results"]["(code, new_tests)"] = res1

            next_phase = False
            if evaluated == 0:
                # loggin ----------
                with open(os.path.join(output_path, "errors.txt"), "a") as f:
                    f.write(f"S1 Error in tests")
                    f.write(f"{str(res1)}")
                    f.write("------\nTests:\n")
                    f.write(f"{d['new_tests']}\n")
                    f.write("------\nCode:\n")
                    f.write(d["code"])
                    if comp_errors:
                        f.write("\n------\nCompiler errors:\n")
                        f.write(comp_errors)
                    else:
                        f.write("\n-------\nNo Compiler errors.  check log\n")
                    f.write("-----------------------\n")

                output_string = f"NoInco; error; step 1 (C +T'); {str(amount_res)}; ; "
                print(output_string)

            elif evaluated == 1:
                output_string = f"NoInco; pass; step 1 (C +T'); {str(amount_res)}; ; "
                print(output_string)

            else:
                next_phase = True
            save_dicts_list_to_json([d], ana_path)

            if next_phase:
                # generate new code  -----------------------------------------------------------------------------------------------
                print("    Step 2 - New Code")
                d["results"]["(new_code, new_tests)"] = [[], [], []]
                new_code, response = self.generator.generate_code(d, input_path, output_path)

                d["new_code"] = new_code
                d["new_code_response"] = response
                print("      execute new code (with new tests)")

                d["new_tests"] = d["new_tests"].replace(test_class_real_name, test_class_unique_name)
                d["test_file_path"] = d["test_file_path"].replace(test_class_real_name, test_class_unique_name)

                exec_results: ExecutionResults = self.executor.execute("new_code", "new_tests", d, input_path, output_path)

                with open(os.path.join(output_path, "log.txt"), "a") as f:
                    f.write("Results after step 2\n")
                    f.write(str(exec_results))

                if exec_results is None:
                    res2 = [[], [], []]
                    comp_errors = None
                else:
                    res2 = exec_results.results
                    comp_errors = exec_results.comp_errors

                d["new_tests"] = d["new_tests"].replace(test_class_unique_name, test_class_real_name)
                d["test_file_path"] = d["test_file_path"].replace(test_class_unique_name, test_class_real_name)
                if comp_errors:
                    comp_errors = comp_errors.replace( test_class_unique_name , test_class_real_name )

                    with open(os.path.join(output_path, "log.txt"), "a") as f:
                        f.write(f"Results after new Code execution:\n")
                        f.write(str(exec_results))

                evaluated2 = self.evaluate(res2)

                save_dicts_list_to_json([d], ana_path)

                amount_res2 = exec_results.results_numbers
                d["results"]["(new_code, new_tests)"] = res2

                if evaluated2 == 0:
                    # loggin ----------
                    with open(os.path.join(output_path, "errors.txt"), "a") as f:
                        f.write(f"S2 Error in code?")
                        f.write(f"{str(res1)}")
                        f.write("------\nTests:\n")
                        f.write(f"{d['new_tests']}\n")
                        f.write("------\nCode:\n")
                        f.write(d["code"])
                        if comp_errors:
                            f.write("\n------\nCompiler errors:\n")
                            f.write(comp_errors)
                        else:
                            f.write("\n-------\nNo Compiler errors.  check log\n")
                        f.write("-----------------------\n")

                    output_string = f"NoInco; error; step 2 (C'+T'); {str(amount_res)}; {str(amount_res2)}"
                    metric_lengths = ""
                    print(output_string)

                else:
                    # calculate the new improved metrix for checking out if something is a positive or not.
                    r1 = [d["results"]["(code, new_tests)"][0],
                          d["results"]["(code, new_tests)"][1] + d["results"]["(code, new_tests)"][2]]
                    r2 = [d["results"]["(new_code, new_tests)"][0],
                          d["results"]["(new_code, new_tests)"][1] + d["results"]["(new_code, new_tests)"][2]]

                    metric = {"p2p": [], "f2f": [], "p2f": [], "f2p": []}

                    for i in r1[0]:
                        if i in r2[0]:
                            metric["p2p"].append(i)
                        elif i in r2[1]:
                            metric["p2f"].append(i)
                    for i in r1[1]:
                        if i in r2[0]:
                            metric["f2p"].append(i)
                        elif i in r2[1]:
                            metric["f2f"].append(i)
                    d["metric"] = metric

                    save_dicts_list_to_json([d], ana_path)
                    metric_lengths = ", ".join(f"{k}: {len(v)}" for k, v in metric.items())

                    if evaluated == 1:
                        output_string = f"INCO; pass; step 2 (C'+T'); {str(amount_res)}; {str(amount_res2)}"

                    else:
                        if len(metric["f2p"]) > 0 and len(metric["p2f"]) == 0:
                            output_string = f"INCO; fail; step 2 (C'+T'); {str(amount_res)}; {str(amount_res2)}"
                        else:
                            output_string = f"NoInco; fail; step 2 (C'+T'); {str(amount_res)}; {str(amount_res2)}"

                print(output_string)
                output_string += f"; {metric_lengths}"


            save_dicts_list_to_json([d], ana_path)
            with open(os.path.join(output_path, "result.txt"), "w") as f:
                output_string+= f"; {str('og tests exist' if 'tests' in d else ' no og tests')}; {current_repair_tries}"
                f.write(output_string)
                print("result:" , output_string)

        except Exception as e:
            method_success = False
            with open(os.path.join(output_path, "errors.txt"), "a") as f:
                f.write(str(e) + "\n\n")
                traceback.print_exc(file=f)

        record_metric_event(
            "analysis_method",
            sample_id=d.get("id"),
            method_name=d.get("signature", {}).get("name"),
            index=0,
            elapsed_seconds=time.perf_counter() - method_start,
            success=method_success,
            verdict=output_string,
        )

        with time_block("executor_teardown", item_count=len(data)):
            self.executor.tear_down(data)

    def evaluate(self, res):
        if res[0] == [] and res[1] == [] and res[2] == []:
            print("        Error")
            # error
            return 0
        elif res[1] == [] and res[2] == []:
            print("        Passed")
            # if no errors or failures  then passed
            return 1
        else:
            print("        Failed")
            return -1



    def prepare_data(self, d, input_path, output_path):
        """
        This function prepares the data for the analysis.
        It will check if the data is complete and if not it will try to extract the missing information,
        like the junit version and the test file path.
        """
        def extract_maven_information():
            junit_version, source_dir, test_source_dir = None, None, None
            with tempfile.TemporaryDirectory() as temp_dir:
                try:
                    shutil.copytree(input_path, temp_dir, dirs_exist_ok=True)
                except Exception as e:
                    print("could not copy root path")
                    print(e)
                    return None
                dock_ex = DockerizedWrapper()

                # get the effective pom file, which usually contains the used junit version
                dock_context = {
                    "image": self.image,
                    "directory": temp_dir,
                    "command": "mvn help:effective-pom -Doutput=effective-pom.xml",
                    "path": "/root/effective-pom.xml"
                }
                dock_ex.execute(dock_context, output_path, copy=True)

            try:
                tree = ET.parse(os.path.join(output_path, "effective-pom.xml"))
                root = tree.getroot()

                # Search for JUnit dependency
                # Define namespaces, if they exist in the pom.xml
                namespaces = {'m': 'http://maven.apache.org/POM/4.0.0'}  # Default Maven namespace
                for dependency in root.findall(".//m:dependency", namespaces):
                    group_id = dependency.find("m:groupId", namespaces)
                    artifact_id = dependency.find("m:artifactId", namespaces)
                    if group_id is not None and artifact_id is not None:
                        if group_id.text == "junit" and artifact_id.text == "junit":
                            version = dependency.find("m:version", namespaces)
                            if version is not None:
                                junit_version = version.text
                            else:
                                junit_version = "JUnit Version not specified"

                # Extract source and test source directories
                build = root.find(".//m:build", namespaces)
                if build is not None:
                    source_directory = build.find("m:sourceDirectory", namespaces)
                    test_source_directory = build.find("m:testSourceDirectory", namespaces)

                    source_dir = source_directory.text if source_directory is not None else None
                    test_source_dir = test_source_directory.text if test_source_directory is not None else None

                # Return a 3-tuple with JUnit version, source directory, and test source directory
                print("::::::::::::::::::::::::::found junit version:", junit_version)
                return junit_version, source_dir, test_source_dir

            except Exception as e:
                print("::::::::::::::::::::::::::found junit version:", "could not parse pom.xml")
                print(e)
                return f"Error parsing pom.xml: {e}", None, None

        # Start of the actual function -----------------------------------

        junit_version, source_dir, test_source_dir = None, None, None
        if  "junit_version" not in d or "test_file_path" not in d:
            print("extracting Junit version")
            junit_version, source_dir, test_source_dir = extract_maven_information()
            if junit_version is None:
                print("could not extract junit version from maven project. Probably is Junit 5\n")
                junit_version = "5.0"
            print(f"Used Junit Version: {junit_version}")

        else :
            junit_version = d["junit_version"]

        print("prepare data")
        if "junit_version" not in d or "test_file_path" not in d:
            d["junit_version"] = junit_version

            if test_source_dir is not None and source_dir is not None:
                d["test_file_path"] = d["code_file_path"].replace(source_dir.replace("/root/", ""), test_source_dir.replace("/root/", ""))
                d["test_file_path"] = d["test_file_path"].replace(".java", "Test.java")
            else:
                d["test_file_path"] = d["code_file_path"].replace(".java", "Test.java")

        if "test_package" not in d:
            d["test_package"] = d["package"]

        # search for junit specific imports   if they are not there add them
        d["test_imports"] = d.get("test_imports", [])

        junit_found = False
        for imp in d["test_imports"]:
            if "junit" in imp:
                junit_found = True
                break

        if not junit_found:
            if d["junit_version"].startswith("3"):
                d["test_imports"].append("import junit.framework.*;\n")
            elif d["junit_version"].startswith("4."):
                d["test_imports"].append("import org.junit.*;\n")
                d["test_imports"].append("import static org.junit.Assert.*;\n")
            else:
                d["test_imports"].append("import org.junit.jupiter.api.*;\n")

        return d
