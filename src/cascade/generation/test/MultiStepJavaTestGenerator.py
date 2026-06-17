import copy
import json
import os
import re
import subprocess
#import tiktoken

from cascade.generation.Generator import Generator
from cascade.generation.executor.OpenAICaller import OpenAICaller
from cascade.utils.JavaUtils import build_context, check_syntax, repair_helper_functions, get_repair_helper_functions, \
    build_signature
from cascade.utils.Metrics import metric_context


class MultiStepJavaTestGenerator(Generator):
    def __init__(self,
                 model="gpt-4o-mini-2024-07-18",
                 max_attempts=1, delay=3,
                 max_tokens=16000,
                 temperature=0,
                 max_prompt_tokens=8000,
                 freq_penalty=0.0, dummy=False,
                 base_url=None, api_key=None #Base url if used with vllm,  for example: "http://127.0.0.1:8000/v1"
                 ):

        super().__init__()
        self.prompt_executor = OpenAICaller(max_attempts=max_attempts, model=model,
                                            max_tokens=max_tokens, temperature=temperature,
                                            delay=delay, freq_penalty=freq_penalty, dummy=dummy,
                                            api_key=api_key, base_url=base_url)

        self.model = model
        self.max_prompt_tokens = max_prompt_tokens

        self.is_junit3 = False

    def build_prompt(self, context):
        # enc = tiktoken.encoding_for_model(self.model)   # this could be used to ensure the prompt is not too long.

        test_framework_instruction = ""
        if "junit_version" in context:
            version = context["junit_version"]
            test_framework_instruction = " Use JUnit version " + (version if version[0].isdigit() else "5") + "."

        par = context['signature']['params']
        params = ", ".join(par) if len(par) > 1 else (par[0] if par else "")

        system_prompt = (
            f"You are an expert Java developer. You will generate JUnit tests for a specific method in a provided test class.{test_framework_instruction} "
            "You can import anything from the project itself. Make sure to handle all exceptions properly, and ensure that all method signatures and calls are correct. "
            "The code should compile on its own without errors."
            )

        #
        class_level_prompt = (
            f"The interesting function under test is:\n```Java\n{build_signature(context, doc=True)}```\n\nThe other methods will be tested later. " 
            "Fill all tests in the provided test class below. This is for test driven development so the tests should be designed to fail if the later implementation does not exactly conform to the documentation.\n"
            f"This is the parent class the method under test resides in:\n## Parent class\n```java\n{build_context(context, doc=True, imports=True, no_fields=False, no_constructors=False, no_other_method_docs=True, no_other_methods=True)}"
            " {\n        // this is the function to be tested\n\n}\n}\n```\n\n"
            )

        test_header = (
            "Make changes or add classes to the imports if necessary. Every object you use has to be properly instantiated, every method has to be imported. "
            "Handle any checked exceptions using try-catch or throws, do not forget type parameters. Match method signatures exactly when overriding or implementing methods. "
            f"Respond with the filled Test Class:\n"
            )

        test_level_prompt = test_header + "\n```java\n" + self.build_tests(context) + "\n```"

        prompt = class_level_prompt + test_level_prompt

        promptlist = []
        promptlist.append({"role": "system", "content": system_prompt})
        promptlist.append({"role": "user", "content": prompt})

        return promptlist

    def generate(self, context, input_path, output_path, response_step2=None):
        results_path = os.path.join(output_path, "results.txt")
        errors_path = os.path.join(output_path, "errors.txt")

        chat_history = []
        print("      Test generation Phase 1")
        # first given the method documentation and signature, we want to extract possible testcases or properties.
        prompt_step1 = [
            {"role": "system",
             "content": "You are an expert Java developer and requirements engineer. You will be given a method signature and its documentation. Your task is to extract behavior specifications from the documentation that can be turned into unit tests to ensure the code is bug free and faithful to its documentation."},
            {"role": "user",
             "content": f"Give a complete description of the behavior that we should test when we want to asure that the code matches its documentation from the following Java method:\n```java\n{build_signature(context, doc=True)}\n```\n\nMake sure you consider the entire functionality exactly as described in the documentation, and cover all edge cases but make no assumptions that are not stated in the documentation."}
        ]

        chat_history.append(copy.deepcopy(prompt_step1))
        with metric_context(phase="generate_tests.phase_1_specs"):
            response_step1a = self.prompt_executor.execute(prompt_step1).model_dump()
        chat_history.append(response_step1a)

        if not response_step1a["choices"]:
            print("      error during generation")
            with open(errors_path, "a") as f:
                f.write(f"error during test generation of {context["signature"]["name"]}")

            return "", chat_history

        prompt_step1.append(response_step1a["choices"][0]["message"])

        # now the goal is to convert this text into a usable format and extract the testable properties
        prompt_json_list = {"role": "user", "content": f"Now turn this into a JSON array of unit tests we should write for test driven development. Each entry in the array should have: \"test_name\": a descriptive test method name starting with 'test' and \"test_description\": a detailed description for the developer of what this tests should do and which specific behavior from the documentation it tests. In particular, I want testable statements of the 'if this then that' type.\nFocus on those tests that follow directly from the documentation, e.g. no performance based ones."}

        # possible alterations to later filter out unnecessary tests
        # To ensure the correctness of the `uniqueIterable` method, we can derive several testable behavior specifications based on the provided documentation.
        # Here are the key behaviors to test, structured in an "if this then that" format:
        # classes:
        #  - "directly from documentation"
        #  - "additional meaningful tests"
        #  - "performance and integration"
        #  - "compile time tests (e.g. for return types)"

        prompt_step1.append(prompt_json_list)

        with metric_context(phase="generate_tests.phase_1_json_tests"):
            response_step1b = self.prompt_executor.execute(prompt_step1).model_dump()
        response_text = response_step1b["choices"][0]["message"]["content"]


        test_list = self.extract_json_list(output_path, response_text)

        chat_history.append(copy.deepcopy(prompt_step1))
        chat_history.append(response_step1b)

        if not test_list:
            with open(errors_path, "a") as f:
                f.write("error during test extraction from json")
            return "", chat_history


        context["test_list"] = test_list

        print("      Test generation Phase 2")
        # now we have a list of testable properties, we want to generate a testclass filled with these.
        prompt_step2 = self.build_prompt(context)

        with metric_context(phase="generate_tests.phase_2_test_class"):
            response_step2a = self.prompt_executor.execute(prompt_step2).model_dump()

        prompt_step2.append(response_step2a["choices"][0]["message"])

        prompt_step2.append({"role": "user", "content": "Make sure that this class compiles without errors. Check if everything that is used is imported correctly and all exceptions are properly caught. Reply with the correct class only"})

        with metric_context(phase="generate_tests.phase_2_compile_check"):
            response_step2b = self.prompt_executor.execute(prompt_step2).model_dump()
        chat_history.append(copy.deepcopy(prompt_step2))
        chat_history.append(response_step2b)

        new_tests = self.extract_tests(response_step2b["choices"][0]["message"]["content"], context, response_step2b, output_path)

        # this is a fallback if the second reply did not include a code block
        if new_tests == "":
            new_tests = self.extract_tests(response_step2a["choices"][0]["message"]["content"], context, response_step2b, output_path)
        # prompt_step2.append({"role": "assistant", "content": f"```java\n{new_tests}\n```"})

        if new_tests == "":
            with open(results_path, "w") as f:
                f.write("Negative, No syntactically correct test class generated")
            with open(errors_path, "w") as f:
                f.write(f"No syntactically correct test class generated \nResponse text:\n{response_text}")
        print("      Test generation finished")
        return new_tests, chat_history


    def build_tests(self, context):
        packg_declaration = f"package {context['test_package']};\n\n"
        imports = "".join(context["test_imports"]) + "\n" + "// add all other needed imports here\n\n"

        # check if we are using junit 3 as there is a difference in structure
        for import_ in context["test_imports"]:
            if ("junit.framework") in import_:
                self.is_junit3 = True
                break

        #class_name = context["test_file_path"].split("/")[-1].split(".")[0]
        class_name = os.path.splitext(os.path.basename(context["test_file_path"]))[0]

        functions = ""
        if self.is_junit3:
            class_definition = f"public class {class_name} extends TestCase {{"
            test_suite_method = f"\n    public {class_name}(String testName) {{\n        super(testName);\n    }}\n\n    public static Test suite() {{\n        return new TestSuite({class_name}.class);\n    }}\n"

            class_definition = class_definition + test_suite_method

            for test in context["test_list"]:
                functions += f"\n    public void {test['test_name']}() {{\n        // {test['test_description']}\n    }}\n\n"

        else:
            class_definition = f"public class {class_name} {{"

            for test in context["test_list"]:
                functions += f"\n    @Test\n    public void {test['test_name']}() {{\n        // {test['test_description']}\n    }}\n\n"

        return packg_declaration + imports + class_definition + functions + "\n}"


    def extract_tests(self, new_tests, context, response, output_path):
        code_blocks = re.findall(r"```java(.*?)\n\s*```", new_tests, flags=re.DOTALL)
        new_tests = ""

        if code_blocks:
            sorted_code_blocks = sorted(code_blocks, key=len, reverse=True)
            for code_block in sorted_code_blocks:
                if check_syntax(code_block, "class", output_path):
                    new_tests = code_block
                    break
        else:
            print("      no code block could be extracted for generated Tests")
            errors_path = os.path.join(output_path, "errors.txt")
            with open(errors_path, "a") as f:
                f.write(f"Could not get tests from response:\n{response}")


        return new_tests


    def repair(self, context, input_path, output_path, errors, key):
        response_history = []
        tools = get_repair_helper_functions()
        #tools = None

        tree = subprocess.check_output(["tree", "-P", "*.java", "--charset=ascii", input_path]).decode("utf-8")

        system_prompt = "You are an expert Java developer. You will fix compilation errors in a provided test class and return the entire repaired class. Use tools to find out more about classes instead of making assumptions."

        prompt = (f"During the compilation of my test class some errors occurred.\nErrors:\n```\n{errors}\n```\n\nTest Class:\n```java\n{context[key]}\n```\n"
                  "Dont change the content of the tests, but make sure that the class compiles without errors. " 
                  "Check if all necessary imports are present and if all exceptions are properly caught. "
                  f"If you need to add imports, use the following directory structure:\n```\n{tree}\n```\n\nNow fix the class so that it compiles without errors, and respond with the entire fixed class."
                  )

        promptlist = []
        promptlist.append({"role": "system", "content": system_prompt})
        promptlist.append({"role": "user", "content": prompt})

        res = self.prompt_executor.execute(promptlist, tools=tools).model_dump()
        response_history.append(copy.deepcopy(promptlist))
        response_history.append(res)
        # we allow three tool usages before we force a generation
        steps = 3
        for i in range(steps):
            if res["choices"][0]["finish_reason"] == "tool_calls":
                promptlist.append(res['choices'][0]['message'])

                tool_calls = res["choices"][0]["message"]["tool_calls"]

                for tool_call in tool_calls:
                    func = tool_call["function"]["name"]
                    arguments = tool_call["function"]["arguments"]

                    results = repair_helper_functions(func, arguments, input_path, output_path, context)

                    promptlist.append({"role": "tool", "content": json.dumps(results), "tool_call_id": tool_call["id"]})

                if i < steps - 1:
                    res = self.prompt_executor.execute(promptlist, tools=tools).model_dump()
                else:
                    res = self.prompt_executor.execute(promptlist).model_dump()
                response_history.append(copy.deepcopy(promptlist))
                response_history.append(res)

        promptlist.append(res['choices'][0]['message'])
        new_tests = res["choices"][0]["message"]["content"]

        new_tests = self.extract_tests(new_tests, context, res, output_path)

        response_history.append(copy.deepcopy(promptlist))
        response_history.append(res)
        return new_tests, response_history


    def extract_json_list(self, output_path, response_text):
        # extract json list from response
        def log_json_error(error_message):
            """Logs the JSON error to results.txt and errors.txt."""
            print(error_message)
            results_path = os.path.join(output_path, "results.txt")
            errors_path = os.path.join(output_path, "errors.txt")
            with open(results_path, "w") as f:
                f.write("Negative, JSON test extraction error")
            with open(errors_path, "w") as f:
                f.write(f"Could not parse JSON: {error_message}\nResponse text:\n{response_text}")

        json_blocks = re.findall(r"```json\s*(.*?)\s*```", response_text, flags=re.DOTALL)


        if not json_blocks:
            log_json_error("Error extracting JSON block from response")
            return []

        try:
            extracted_test_list = json.loads(json_blocks[0].strip())

        except json.JSONDecodeError as e:
            log_json_error(str(e))
            return []

        # make sure that all tests begin with test (e.g. instead of ending) and adding numbers to test cases that have the name
        # also check if the elements have the correct keys.
        clean_test_list = []
        seen_names = {}
        for et in extracted_test_list:
            if "test_name" in et and "test_description" in et:
                base_name = et["test_name"].replace("test", "").replace("Test", "").replace("TEST", "").strip()
                seen_names[base_name] = seen_names.get(base_name, 0) + 1
                ct = {
                    "test_name": (f"test{base_name}" if seen_names[base_name] == 1 else f"test{base_name}{seen_names[base_name]}"),
                    "test_description": et["test_description"]
                }
                clean_test_list.append(ct)
        if clean_test_list == []:
            log_json_error("No test case with the correct keys found in extracted JSON")
            return []

        test_names = [test['test_name'] for test in clean_test_list]
        print(f"      Got {len(clean_test_list)} potential tests:\n        {'\n        '.join(test_names)}")
        return  clean_test_list
