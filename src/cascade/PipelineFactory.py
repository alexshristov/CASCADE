from cascade.analysis.executor.Execution import Execution
from cascade.generation.code.EmptyGenerator import EmptyGenerator
from cascade.Pipeline import Pipeline
from cascade.generation.Generation import Generation
from cascade.filters.Filter import Filter
from cascade.utils import Utils

import sys

import os
import importlib


class PipelineFactory:
    """
    Factory class for constructing Pipeline instances dynamically from config files.

    The PipelineFactory implements the Factory design pattern to create Pipeline objects
    with all necessary components (Extraction, Generation, Analysis, Execution, and Filters)
    configured from a JSON setup file.
    """

    @staticmethod
    def load_class(class_name, module_path, args, kwargs):
        """
        Dynamically loads a class and instantiates it with given arguments.

        :param
            class_name: The name of the class to be instantiated.
            module_path: The dot-separated path to the module containing the class.
                               For example, 'package.subpackage.module'.
            **kwargs: Arbitrary keyword arguments passed to the class constructor.
        :return
            An instance of the specified class, instantiated with the given arguments.
        """
        try:
            # Dynamically import the module where the class is defined
            module = importlib.import_module(module_path)

            # Get the class by its name
            cls = getattr(module, class_name)

            # Instantiate the class with the provided arguments
            instance = cls(*args, **kwargs)

            return instance
        except ModuleNotFoundError as e:
            print(f"Module '{module_path}' not found: {e}")
            sys.exit(-1)
        except AttributeError as e:
            print(f"Class '{class_name}' not found in '{module_path}': {e}")
            sys.exit(-1)
        except Exception as e:
            print(f"Error instantiating class '{class_name}': {e}")
            sys.exit(-1)

    def build(self, pipeline_path, output_path, kwargs=None):
        """
        Builds a pipeline from a config file and optional keyword arguments.
        The config file is a JSON file containing the specific modules that should be used for each pipeline component.
        It should include Extraction, any of (CodeGenerator, TestGenerator, DocGenerator), Analysis and its Executor

        :param pipeline_path: The path to the config file.
        :param output_path: Directory where output files and metrics will be written.
        """
        if not kwargs:
            kwargs = {"module_path": None, "Extraction": {}, "CodeGenerator": {}, "TestGenerator": {},
                      "DocGenerator": {}, "Analysis": {}, "Executor": {}, "FilterFunctions": []}

        config = Utils.load_json_from_path(pipeline_path)
        if not config:
            raise Exception("No config file found at:" , pipeline_path)

        if kwargs["module_path"]:
            sys.path.append(os.path.abspath(str(kwargs["module_path"])))
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cascade")))

        name = config["Extraction"]["name"]
        path = "cascade.extraction." + name
        kwargs_ = config["Extraction"]["kwargs"]
        kwargs_.update(kwargs["Extraction"])
        extraction = self.load_class(name, path, [], kwargs_)

        filter_functions = []
        if "FilterFunctions" in config:
            for f in config["FilterFunctions"]:
                name = f["name"]
                path = "cascade.filters." + name
                kwargs_ = f["kwargs"]
                index = config["FilterFunctions"].index(f)
                if len(kwargs["FilterFunctions"]) > index:
                    kwargs_.update(kwargs["FilterFunctions"][index])
                filter_functions.append(self.load_class(name, path, [], kwargs_))

        filter_ = Filter(filter_functions)

        generator_strings = [("CodeGenerator", "code"),
                             ("TestGenerator", "test"),
                             ("DocGenerator", "doc")]

        generators = {gen[1]: EmptyGenerator() for gen in generator_strings}
        for gen in generator_strings:
            if gen[0] in config and "name" in config[gen[0]]:
                current = config[gen[0]]
                name = current["name"]
                path = f"cascade.generation.{gen[1]}." + name
                kwargs_ = current["kwargs"]
                kwargs_.update(kwargs[gen[0]])
                generators[gen[1]] = self.load_class(name, path, [], kwargs_)

        generation = Generation(generators["code"], generators["test"], generators["doc"])

        name = config["Executor"]["name"]
        path = "cascade.analysis.executor." + name
        kwargs_ = config["Executor"]["kwargs"]
        kwargs_.update(kwargs["Executor"])
        analysis_executor = self.load_class(name, path, [], kwargs_)

        name = config["Analysis"]["name"]
        path = "cascade.analysis." + name
        kwargs_ = config["Analysis"]["kwargs"]
        kwargs_.update(kwargs["Analysis"])
        analysis = self.load_class(name, path, [generation, Execution(analysis_executor)], kwargs_)

        if "MetricsRecorder" in config:
            rec_cfg = config["MetricsRecorder"]
            rec_name = rec_cfg["name"]
            rec_path = rec_cfg.get("module_path", "cascade.metrics." + rec_name)
            rec_kwargs = rec_cfg.get("kwargs", {})
        else:
            rec_name = "MetricsRecorder"
            rec_path = "cascade.metrics.MetricsRecorder"
            rec_kwargs = {}
        recorder = self.load_class(rec_name, rec_path, [output_path], {"config_snapshot": config, **rec_kwargs})

        pipeline = Pipeline(extraction, filter_, analysis, config, recorder)

        return pipeline


