import copy

from cascade.generation.Generator import Generator
from cascade.utils.Metrics import metric_context, time_block


class Generation:
    """
    This is the wrapper class for all types of generators
    """
    def __init__(self, code_generator: Generator, test_generator: Generator, doc_generator: Generator):
        """
        Constructor for the general Generation class that takes in three generators as parameters and assigns them to the class.

        if one is not needed for the analysis on hand the 'EmptyGenerator' class can be used instead.

        :param code_generator: The generator that generates code
        :param test_generator: The generator that generates tests
        :param doc_generator: The generator that generates documentation
        """

        self.code_generator = code_generator
        self.test_generator = test_generator
        self.doc_generator = doc_generator

    @staticmethod
    def _context_fields(context, component, phase):
        return {
            "sample_id": context.get("id"),
            "method_name": context.get("signature", {}).get("name"),
            "component": component,
            "phase": phase,
        }

    def generate_code(self, context, input_path, output_path):
        """
        code generation method
        :param input_path:
        """
        context_ = copy.deepcopy(context)
        fields = self._context_fields(context, self.code_generator.__class__.__name__, "generate_code")
        with metric_context(**fields):
            with time_block("generation", generator_kind="code"):
                code, response = self.code_generator.generate(context_, input_path, output_path)
        del context_
        return code, response

    def generate_tests(self, context, input_path, output_path):
        context_ = copy.deepcopy(context)
        fields = self._context_fields(context, self.test_generator.__class__.__name__, "generate_tests")
        with metric_context(**fields):
            with time_block("generation", generator_kind="test"):
                tests, response = self.test_generator.generate(context_, input_path, output_path)
        del context_
        return tests, response

    def generate_doc(self, context, input_path, output_path):
        context_ = copy.deepcopy(context)
        fields = self._context_fields(context, self.doc_generator.__class__.__name__, "generate_doc")
        with metric_context(**fields):
            with time_block("generation", generator_kind="doc"):
                doc, response = self.doc_generator.generate(context_, input_path, output_path)
        del context_
        return doc, response

    def repair_tests(self, context, input_path, output_path, errors, key):
        context_ = copy.deepcopy(context)
        fields = self._context_fields(context, self.test_generator.__class__.__name__, "repair_tests")
        with metric_context(**fields):
            with time_block("generation_repair", generator_kind="test", repair_key=key):
                tests, response = self.test_generator.repair(context_, input_path, output_path, errors, key)
        del context_
        return tests, response

    def repair_code(self, context, input_path, output_path, errors, key):
        context_ = copy.deepcopy(context)
        fields = self._context_fields(context, self.code_generator.__class__.__name__, "repair_code")
        with metric_context(**fields):
            with time_block("generation_repair", generator_kind="code", repair_key=key):
                code, response = self.code_generator.repair(context_, input_path, output_path, errors, key)
        del context_
        return code, response
