import copy
import json
import os
import re
import subprocess
from platform import system

import tiktoken

from cascade.generation.Generator import Generator
from cascade.generation.executor.OpenAICaller import OpenAICaller
from cascade.utils.JavaUtils import (
    build_context,
    check_syntax,
    repair_helper_functions,
    get_repair_helper_functions,
)


class GPT4JavaTestGenerator(Generator):
    """deprecated"""

    def __init__(
        self,
        max_attempts=1,
        max_tokens=16000,
        temperature=0,
        delay=3,
        max_prompt_tokens=5000,
        model="gpt-4o-mini-2024-07-18",
        freq_penalty=0.0,
        dummy=False,
        ask_for_imports=False,
        import_prompt_finisher="Reply with the missing imports, leave out those you don't know the correct package of.",
    ):
        super().__init__()
        self.ask_for_imports = ask_for_imports
        self.model = model
        self.import_prompt_finisher = import_prompt_finisher
        self.max_prompt_tokens = max_prompt_tokens
        self.prompt_executor = OpenAICaller(
            max_attempts=max_attempts,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            delay=delay,
            freq_penalty=freq_penalty,
            dummy=dummy,
        )

        self.is_three = False

    def build_prompt(self, context):
        enc = tiktoken.encoding_for_model(self.model)

        testframework = ""
        if "junit_version" in context:
            version = context["junit_version"]
            testframework = "Use JUnit version " + (
                version if version[0].isdigit() else "5"
            )

        par = context["signature"]["params"]
        params = ", ".join(par) if len(par) > 1 else (par[0] if par else "")

        # system_prompt = f"Write Java tests for the function {context['signature']['name']}. Follow its documentation as closely as possible."
        system_prompt = f"You are a Java developer assistant. You will generate unit tests for a specific method in a provided class. The method is not implemented yet so you will be using only its documentation as the ground truth of the expected behavior. {testframework}. You can import anything from the project, but no third party libraries. Handle exceptions properly, and ensure method signatures and calls are correct. The code should compile without errors. Focus on creating tests that cover edge cases, boundary conditions, and all documented behaviors, including thrown exceptions"

        c1 = f"The function under test is `{context['signature']['name']}({params})`\n\nThis is for test driven development so the tests should be designed to fail if the later implementation does not conform to the documentation. Here is the class containing the function:\n\n```java\n"
        c2 = "; // this is the function to be tested\n\n}\n```\n"
        code = c1 + build_context(context, doc=True) + c2

        # test_header = "\n\n// TESTS:\n\n" + self.build_tests(context, primer=f"\n    // write tests for {context['signature']['name']} here. Take the Documentation as literal as possible.\n")

        test_header = f"\nNow Please write several tests for the function `{context['signature']['name']}({params})` using the following test class skeleton. Do not rename the class. Everything that you use has to be added to the imports. Properly handle any checked exceptions (use `try-catch` or `throws`), don't forget type parameters. Match method signatures exactly when overriding or implementing methods. Adhere to the documentation as close as possible when writing the tests and try to test everything that is mentioned including normal operation, edge cases, and error conditions. As a reminder, the documentation for the function is:\n\n```java\n{context['doc']}\n```\n\n Test class:\n"
        test_header = (
            test_header
            + "\n```java\n"
            + self.build_tests(
                context,
                primer=f"\n    // write tests for {context['signature']['name']} here."
                + "\n```",
            )
        )

        if self.is_three:
            test_header = test_header
            # add something like:    Include necessary constructors if extending a class that requires them.

        prompt = code + test_header

        if len(enc.encode(prompt)) > self.max_prompt_tokens:
            code = c1 + build_context(context, doc=True, no_fields=True) + c2
            prompt = code + test_header

        if len(enc.encode(prompt)) > self.max_prompt_tokens:
            code = (
                c1
                + build_context(context, doc=True, no_fields=True, no_constructors=True)
                + c2
            )
            prompt = code + test_header

        if len(enc.encode(prompt)) > self.max_prompt_tokens:
            code = (
                c1
                + build_context(
                    context,
                    doc=True,
                    no_fields=True,
                    no_constructors=True,
                    no_other_method_docs=True,
                )
                + c2
            )
            prompt = code + test_header

        if len(enc.encode(prompt)) > self.max_prompt_tokens:
            code = (
                c1
                + build_context(
                    context,
                    doc=True,
                    no_fields=True,
                    no_constructors=True,
                    no_other_method_docs=True,
                    no_other_methods=True,
                )
                + c2
            )
            prompt = code + test_header

        if len(enc.encode(prompt)) > self.max_prompt_tokens:
            return []

        promptlist = []
        promptlist.append({"role": "system", "content": system_prompt})
        promptlist.append({"role": "user", "content": prompt})

        return promptlist

    def build_tests(self, context, primer=""):
        packg_declaration = f"package {context['test_package']};\n\n"
        imports = "".join(context["test_imports"]) + "\n"

        for import_ in context["test_imports"]:
            if ("junit.framework") in import_:
                self.is_three = True
                break

        name = str(context["signature"]["name"])
        class_name = context["test_file_path"].split("/")[-1].split(".")[0]
        if self.is_three:
            classdefinition = "public class " + class_name + " extends TestCase {"
            test_suite_method = (
                "\n    public"
                + class_name
                + "(String name) {\n        super(name);\n    }\n\n    public static Test suite() {\n        return new TestSuite("
                + class_name
                + ".class);\n    }\n"
            )

            classdefinition = classdefinition + test_suite_method
            func_definition = (
                "\n    public void test" + name[0].upper() + name[1:] + "1(){"
            )
        else:
            classdefinition = "public class " + class_name + "{"
            func_definition = (
                "\n    @Test\n    public void test"
                + name[0].upper()
                + name[1:]
                + "1(){"
            )
        return packg_declaration + imports + classdefinition + primer + func_definition

    def generate(self, context, input_path, output_path):
        prompt = self.build_prompt(context)

        response = self.prompt_executor.execute(prompt).model_dump()
        new_tests = self.extract_tests(
            response["choices"][0]["message"]["content"], context, response, output_path
        )

        prompt.append({"role": "assistant", "content": f"´´´java\n{new_tests}\n```"})

        imports = dict()

        # extract all 'new' statements.
        calls = re.findall(r"new (.*?)\(", new_tests, flags=re.DOTALL)

        tree = subprocess.check_output(
            ["tree", "-P", "*.java", "--charset=ascii", input_path]
        ).decode("utf-8")

        repair_question = f"{', '.join(calls)} {'are' if len(calls) > 1 else 'is'} new, fix all missing imports using this directory structure:\n```\n{tree}\n```"

        prompt.append({"role": "user", "content": repair_question})

        repair_response = self.prompt_executor.execute(prompt).model_dump()

        prompt.append(repair_response["choices"][0]["message"])

        # if self.ask_for_imports or (len(context["test_imports"]) == 1 and "*" in context["test_imports"][0]):
        #     prompt.append({"role" : "assistant", "content" : response["choices"][0]["message"]["content"]})
        #     prompt.append({"role" : "user", "content" : f"Give me the missing imports for this code to work. For the tests, I am already importing:\n```java\n{''.join(context['test_imports'])}\n```\n\n The tested class imports:\n```java\n{''.join(context['parent']['imports'])}\n```\n\n{self.import_prompt_finisher}" })
        #     imports = self.prompt_executor.execute(prompt).model_dump()
        #     imports_message = imports["choices"][0]["message"]["content"]
        #     for line in imports_message.splitlines():
        #         if "import" in line and ";" in line:
        #             context["test_imports"].append(line + "\n")
        #     context["test_imports"] = list(set(context["test_imports"]))

        # r

        new_tests = repair_response["choices"][0]["message"]["content"]
        new_tests = self.extract_tests(new_tests, context, repair_response, output_path)

        response_for_logging = {
            "prompt": prompt,
            "response": response,
            "repair": repair_response,
            "imports": imports,
        }

        return new_tests, response_for_logging

    def extract_tests(self, new_tests, context, response, output_path):
        code_blocks = re.findall(r"```java(.*?)\n\s*```", new_tests, flags=re.DOTALL)

        if code_blocks:
            new_tests = code_blocks[0]
        else:
            new_tests = new_tests.split("```java")[-1].strip()

        new_tests = self.try_to_fix(new_tests, response, context, output_path)

        return new_tests

    def try_to_fix(self, new_tests, response, context, output_path):
        # check if the class is complete
        chunk = ""
        braces = 2
        for letter in new_tests:
            chunk += letter
            if letter == "{":
                braces += 1
            elif letter == "}":
                braces -= 1
            if braces == 0:
                break

        # we have to complete the class
        if braces == 0:
            return self.build_tests(context) + chunk

        if braces == 1:
            # two possible cases  full class with a brace too much   or a completion with a brace to few
            # full class
            to_check = [
                chunk[: chunk.rfind("}")],
                self.build_tests(context) + chunk + "}",
                self.build_tests(context) + chunk[chunk.find("{") + 1 :],
            ]
            for check in to_check:
                if check_syntax(check, "class", output_path):
                    return check

        # the class is complete
        if braces == 2:
            check = chunk
            if check_syntax(check, "class", output_path):
                return check
            check = self.build_tests(context) + chunk[chunk.find("{") + 1 :] + "}"
            if check_syntax(check, "class", output_path):
                return check

        if braces > 2:
            if response["choices"][0]["finish_reason"] == "length":
                last_test = 0
                lines = new_tests.splitlines()

                for num, line in enumerate(lines):
                    if not self.is_three:
                        if "@Test" in line:
                            last_test = num
                    else:
                        if "public void test" in line:
                            last_test = num

                return "\n".join(lines[:last_test]) + "\n}"

        return new_tests + "}" * (braces - 2)

    def repair(self, context, input_path, output_path, errors, key):
        tools = get_repair_helper_functions()

        tree = subprocess.check_output(
            ["tree", "-P", "*.java", "--charset=ascii", input_path]
        ).decode("utf-8")

        system_prompt = "You are an expert Java developer. Fix compilation errors in the provided test class. Use tools to find out more about classes instead of making assumptions."

        prompt = f"The following errors occurred during compilation.\n```\n{errors}\n```\nThis is the project structure:\n```\n{tree}\n```\n\nFix the errors in the following test class:\n```java\n{context[key]}\n```\n.The documentation for the function under test is:\n```java\n{context['doc']}\n``` Adhere to this documentation as close as possible."

        promptlist = []
        promptlist.append({"role": "system", "content": system_prompt})
        promptlist.append({"role": "user", "content": prompt})

        res = self.prompt_executor.execute(promptlist, tools=tools).model_dump()

        steps = 3
        for i in range(steps):
            if res["choices"][0]["finish_reason"] == "tool_calls":
                promptlist.append(res["choices"][0]["message"])

                tool_calls = res["choices"][0]["message"]["tool_calls"]

                for tool_call in tool_calls:
                    func = tool_call["function"]["name"]
                    arguments = tool_call["function"]["arguments"]

                    results = repair_helper_functions(
                        func, arguments, input_path, output_path, context
                    )

                    promptlist.append(
                        {
                            "role": "tool",
                            "content": json.dumps(results),
                            "tool_call_id": tool_call["id"],
                        }
                    )

                if i < steps - 1:
                    res = self.prompt_executor.execute(
                        promptlist, tools=tools
                    ).model_dump()
                else:
                    res = self.prompt_executor.execute(promptlist).model_dump()

        promptlist.append(res["choices"][0]["message"])

        repair_response = {"prompt": promptlist, "response": res}

        new_tests = res["choices"][0]["message"]["content"]

        new_tests = self.extract_tests(new_tests, context, res, output_path)

        return new_tests, repair_response
