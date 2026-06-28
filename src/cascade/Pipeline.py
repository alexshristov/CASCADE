import os

from cascade.extraction.Extraction import Extraction
from cascade.filters.Filter import Filter
from cascade.analysis.Analysis import Analysis
from cascade.utils.Utils import load_json_from_path
from cascade.metrics.BaseMetricsRecorder import BaseMetricsRecorder
from cascade.utils.Metrics import record_metric_event, reset_current_recorder, set_current_recorder, time_block


class Pipeline():
    def __init__(self, extraction: Extraction, _filter: Filter, analysis: Analysis, setup_config: dict,
                 recorder: BaseMetricsRecorder):
        """
        The main pipeline object. Calls "extract" and "analyse" in an appropriate manner.
        is usually build through Pipeline_Factory
        :param extraction: the specific instantiated Extraction object that is used for extraction
        :param analysis: the specific instantiated analysis object
        :param setup_config: a dictionary that contains the names of the specific instances used
         for extraction, analysis and the objects inside of them
        :param recorder: metrics recorder instance created by PipelineFactory
        """
        self.extraction = extraction
        self._filter = _filter
        self.analysis = analysis
        self.setup_config = setup_config
        self.recorder = recorder

    def execute(self, input_path, output_path) -> None:
        """
        This executes the entire pipline. First extract() from the extraction object is called.
        he output of that is passed to the analysis object. and analyze is executed.

        These specific objects handle what the specific operations do and any things like temporary or
        intermediate saving, which type of analyses should be done and the generator that the analysis uses.
        """
        recorder = self.recorder
        recorder_token = set_current_recorder(recorder)
        success = True
        try:
            with time_block("pipeline_total", input_path=input_path, output_path=output_path):
                if not os.path.exists(os.path.join(output_path, "analyzed.json")):
                    print("Extraction started")
                    with time_block("extraction"):
                        data = self.extraction.extract(input_path, output_path)
                    print("Extraction finished. Extracted: ", len(data))
                    record_metric_event("extraction_result", extracted_count=len(data))

                    print("Filtering started")
                    with time_block("filtering", input_count=len(data)):
                        filtered_data = self._filter.filter_all(data)
                    print("Filtering finished. Remaining: ", len(filtered_data))
                    record_metric_event("filtering_result", input_count=len(data), output_count=len(filtered_data))

                else:
                    print("Found analyzed results, will skip extraction and filtering")
                    # generated artifacts for the same dataset can be saved to avoid repeated generation of code and tests.
                    with time_block("load_existing_analyzed"):
                        temp_data = load_json_from_path(os.path.join(output_path, "analyzed.json"))
                    filtered_data = []
                    if temp_data:
                        filtered_data = temp_data
                    record_metric_event("resume_from_analyzed", loaded_count=len(filtered_data))

                if not filtered_data:
                    print("No data to analyze")
                    record_metric_event("analysis_skipped", reason="no_data")
                    return

                print("Analysis started")
                with time_block("analysis", item_count=len(filtered_data)):
                    self.analysis.analyze(filtered_data, input_path, output_path)
                print("Analysis finished")
        except Exception:
            success = False
            raise
        finally:
            record_metric_event("run_finished", success=success)
            recorder.write_summary()
            reset_current_recorder(recorder_token)
