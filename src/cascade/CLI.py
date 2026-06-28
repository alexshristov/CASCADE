import ast
from cascade.build import build

from argparse import ArgumentParser

from cascade.PipelineFactory import PipelineFactory


def main():
    program_name = "CASCADE"
    description = ""
    epilog = ("To overwrite key word arguments one has to write \"key:value\", except for the filters, where one has "
              "to write \"(index,key:value)\"")
    arg_parser = ArgumentParser(prog=program_name,
                                description=description,
                                epilog=epilog)
    subparsers = arg_parser.add_subparsers(title='subcommands',
                                       description='valid subcommands',
                                       help='additional help')
    run_ = subparsers.add_parser("run")

    run_.add_argument('-i', '--input-path', required=True,
                            help="The input path which will be used for extraction")
    run_.add_argument('-o', '--output-path', required=True,
                            help="The output path in which results and temporary files will be stored")
    run_.add_argument('-c', '--config-file', required=True,
                            help="The path to the config file defining the pipeline")
    run_.add_argument('-m', '--module-path', help="The path to the user defined modules.")
    run_.add_argument('-extr', '--extraction', action="append",
                            help="A way to overwrite the key word arguments for the extraction.")
    run_.add_argument('-codegen', '--code-generator', action="append",
                            help="A way to overwrite the key word arguments for the code generator.")
    run_.add_argument('-testgen', '--test-generator', action="append",
                            help="A way to overwrite the key word arguments for the test generator.")
    run_.add_argument('-docgen', '--doc-generator', action="append",
                            help="A way to overwrite the key word arguments for the doc generator.")
    run_.add_argument('-ana', '--analysis', action="append",
                            help="A way to overwrite the key word arguments for the analysis.")
    run_.add_argument('-exec', '--executor', action="append",
                            help="A way to overwrite the key word arguments for the executor.")
    run_.add_argument('-filter', '--filters', action="append",
                            help="A way to overwrite the key word arguments for the filters.")

    run_.add_argument('--debug-cli', help="Shows debug information for the CLI call.", action='store_true')
    run_.set_defaults(func=run)

    build_ = subparsers.add_parser("build-sample")
    build_.add_argument('-i', '--input-path', required=True,
                     help="The input path to the root of the analyzed project")
    build_.add_argument('-o', '--output-path', required=True,
                     help="The output path in which results and temporary files will be stored")
    build_.add_argument('-a', '--analyzed-file', required=True,
                     help="The path to the analyzed.json file containing the results")
    build_.add_argument('-id', type=int, required=True,
                        help="The id of the sample to build")
    build_.add_argument('-code-key', required=True,
                        help="The key for the code to put in the project")
    build_.add_argument('-tests-key', required=True,
                        help="The key for the tests to put in the project")

    build_.set_defaults(func=build)

    args = arg_parser.parse_args()
    args.func(args)


def run(args):
    kwargs_overrides = {"Extraction": {}, "CodeGenerator": {}, "TestGenerator": {}, "DocGenerator": {}, "Analysis": {},
                        "Executor": {}, "FilterFunctions": []}
    override_to_arg = {"Extraction": args.extraction, "CodeGenerator": args.code_generator,
                       "TestGenerator": args.test_generator, "DocGenerator": args.doc_generator,
                       "Analysis": args.analysis, "Executor": args.executor}
    for key, value in override_to_arg.items():
        if value:
            for kw in value:
                try:
                    key_value = kw.split(":")

                    try:
                        kwargs_overrides[key][key_value[0]] = ast.literal_eval(":".join(key_value[1:]))
                    except:
                        kwargs_overrides[key][key_value[0]] = ":".join(key_value[1:])

                except Exception as e:
                    print(e)
    if args.filters:
        for tuple_ in args.filters:
            try:
                index = int(tuple_[1:-1].split(",")[0])
                key_value = (",".join(tuple_[1:-1].split(",")[1:])).split(":")
                filters = kwargs_overrides["FilterFunctions"]
                while len(filters) <= index:
                    filters.append({})
                try:
                    filters[index][key_value[0].strip()] = ast.literal_eval(":".join(key_value[1:]))
                except:
                    filters[index][key_value[0].strip()] = ":".join(key_value[1:])

            except Exception as e:
                print(e)

    kwargs_ = {"module_path": args.module_path}
    kwargs_.update(kwargs_overrides)
    if args.debug_cli:
        print("kwargs", kwargs_)

    factory = PipelineFactory()
    pipeline = factory.build(args.config_file, args.output_path, kwargs_)
    pipeline.execute(args.input_path, args.output_path)


if __name__ == '__main__':
    main()
