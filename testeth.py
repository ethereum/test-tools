# coding=utf-8
from __future__ import print_function

import click
import json
import re
import sys
import yaml
from collections import OrderedDict
from os import path, walk
from subprocess import Popen, PIPE
from tabulate import tabulate


# TODO:
# 1. Check a tool overhead by executing execution of empty code.
# 2. Limit the tool name length to make generating reports easier.

class Test(object):
    def __init__(self, desc):
        if 'exec' in desc:
            exc = desc['exec']
            self.code = exc['code'][2:]  # Strip leading 0x prefix
            self.input = exc['data'][2:]
            self.gas = int(exc['gas'], 16)


class Result(object):
    def __init__(self, args, return_code, out, err):
        self.args = args
        self.return_code = return_code
        self.out = out
        self.err = err
        self.result_processing_error = None
        self.time = None

    def value(self):
        """ Return single-value representation to be used in reports."""

        if self.time is not None:
            return self.time * 1000  # Return time in milliseconds.
        if self.return_code != 0 or self.err:
            return "Error"           # Tool returned error.
        return "No timing"           # No timing found in tool output.

    def __str__(self):
        v = self.value()
        if self.time is not None:
            return "{:.3f} ms".format(v)
        return v


def _load_test_file(test_file):
    file_name = path.basename(test_file)
    if file_name.startswith('vmInputLimits'):
        return {}

    _, ext = path.splitext(file_name)
    if ext == '.json':
        json_tests = json.load(open(test_file), object_pairs_hook=OrderedDict)
    else:
        raise ValueError('Unsupported test file format: {}'.format(ext))

    # Add path to the test names to asure uniqueness.
    tests = OrderedDict()
    for name, desc in json_tests.items():
        tests[file_name + '@' + name] = Test(desc)
    return tests


def _load_tests_from_folder(folder):
    tests = OrderedDict()
    for root, _, files in walk(folder):
        for file in files:
            name, _ = path.splitext(file)
            tests.update(_load_test_file(path.join(root, file)))
    return tests


def load_tests(test_path):
    if path.isdir(test_path):
        return _load_tests_from_folder(test_path)
    return _load_test_file(test_path)


class ToolConnector(object):
    @staticmethod
    def get(tool):
        tool_name = path.basename(tool.path)
        return {
            'evm': EvmConnector(),
            'ethvm': EthvmConnector()
        }[tool_name]


class EvmConnector(ToolConnector):
    def preprare_args(self, test):
        args = ['--sysstat',
                '--code',  test.code,
                '--input', test.input,
                '--gas',   str(test.gas)]
        return args

    def process_result(self, result):
        m = re.search('vm took (\d+(?:\.\d+)?)([mµ]?)s', result.out)
        value = float(m.group(1))
        unit = {'': 1, 'm': 1000, 'µ': 1000000}[m.group(2)]
        result.time = value / unit


class EthvmConnector(ToolConnector):
    def preprare_args(self, test):
        gas = test.gas + 50000  # FIXME: ethvm uses gas also on a transaction.
        args = ['bench',
                '--code',  test.code,
                '--input', test.input,
                '--gas',   str(gas)]
        return args

    def process_result(self, result):
        result.time = float(result.out)


class Tool(object):
    def __init__(self, name, path, params):
        self.name = name
        self.path = path
        self.params = params
        self.__conn = ToolConnector.get(self)

    @staticmethod
    def yaml_representer(dumper, obj):
        data = {'name': obj.name, 'path': obj.path, 'params': obj.params}
        return dumper.represent_mapping('!tool', data)

    @staticmethod
    def yaml_constructor(loader, node):
        data = loader.construct_mapping(node)
        return Tool(data['name'], data['path'], data['params'])

    def execute_test(self, test):
        args = self.__conn.preprare_args(test)
        args = [self.path] + list(self.params) + args
        # print(' '.join(args))
        ps = Popen(args, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        out, err = ps.communicate()
        res = Result(args, ps.returncode,
                     out.decode('utf-8'), err.decode('utf-8'))
        try:
            self.__conn.process_result(res)
        except Exception as ex:
            res.result_processing_error = ex
        return res

# FIXME: This is a bit overkill as the config file is not human-readable any
#        more. I think we need to do manual config dumping and loading.
yaml.add_representer(Tool, Tool.yaml_representer)
yaml.add_constructor('!tool', Tool.yaml_constructor)


class Config(object):
    config_file = path.join(path.dirname(__file__), 'testeth.yml')

    def __init__(self):
        self.tools = OrderedDict()

    @classmethod
    def load(cls):
        if path.exists(cls.config_file):
            return yaml.load(open(cls.config_file))
        return cls()

    def save(self):
        yaml.dump(self, open(self.config_file, 'w'))


@click.group()
@click.pass_context
def testeth(ctx):
    ctx.obj = Config.load()


@testeth.command()
@click.pass_obj
@click.argument('test_path', type=click.Path())
def test(config, test_path):
    tests = load_tests(test_path)

    report = OrderedDict()
    report['Tests'] = tests.keys()
    warnings = []
    for tool in config.tools.values():
        print("> {} ...".format(tool.name))
        sys.stdout.flush()
        results = []
        for name, test in tests.items():
            res = tool.execute_test(test)
            results.append(res.value())
            if res.return_code != 0:
                warnings.append("Error {}:\n{}\n*** Command: {}"
                                .format(res.return_code, res.err,
                                        ' '.join(res.args)))
            elif res.result_processing_error:
                warnings.append("Result processing error: {}\n"
                                "*** Output:\n{}\n*** Command: {}"
                                .format(res.result_processing_error, res.out,
                                        ' '.join(res.args)))
        report[tool.name] = results

    print()
    print(tabulate(report, headers='keys', floatfmt=".3f"))

    if warnings:
        print("\nWARNINGS:")
        print(*warnings)


@testeth.group()
def tool():
    pass


@tool.command('list')
@click.pass_obj
def list_tools(config):
    for tool in config.tools.values():
        print("{:<16}{} {}".format(tool.name, tool.path,
                                   ' '.join(tool.params)))


@tool.command('register')
@click.pass_obj
@click.argument('name')
@click.argument('path', type=click.Path())
@click.argument('params', nargs=-1, type=click.UNPROCESSED)
def register_tool(config, name, path, params):
    # TODO: Try get version number
    tool = Tool(name, path, params)
    config.tools[tool.name] = tool
    config.save()


if __name__ == '__main__':
    testeth()
