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
# - Check a tool overhead by executing execution of empty code.
# - Limit the tool name length to make generating reports easier.
# - Add test verification.

class Test(object):
    def __init__(self, desc):
        self.expected = TestResult()
        if 'exec' in desc:  # JSON format.
            exc = desc['exec']
            self.code = exc['code'][2:]  # Strip leading 0x prefix
            self.input = exc['data'][2:]
            self.gas = int(exc['gas'], 16)

            # No expected gas means exception expected
            self.expected.exception = 'gas' not in desc
            if not self.expected.exception:
                self.expected.output = desc['out'][2:]
                gas_left = int(desc['gas'], 16)
                self.expected.gas_used = self.gas - gas_left
        else:
            self.code = desc['code']
            self.gas = desc.get('gas')
            self.input = desc.get('input', '')
            expected = desc.get('expected', {})
            self.expected.output = expected.get('output')
            self.expected.gas_used = expected.get('gas used')
            self.expected.exception = expected.get('exception', False)


class TestResult(object):
    """ Represents test expected/actual result. None value means don't care."""

    def __init__(self):
        self.output = None
        self.gas_used = None
        self.exception = None

    def __eq__(self, other):
        """ Compare test results. If any of attribute values is None ignore
            comparison."""

        for attr, value in self.__dict__.items():
            if value is None:
                continue
            o = getattr(other, attr)
            if o is None:
                continue
            if o != value:
                return False
        return True


class Result(object):
    def __init__(self, args, return_code, out, err):
        self.args = args
        self.return_code = return_code
        self.out = out
        self.err = err
        self.result_processing_error = None
        self.time = None
        self.test_result = TestResult()

    def value(self, expected):
        """ Return single-value representation to be used in reports."""

        if self.test_result != expected:
            return "Failure"
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
        descs = json.load(open(test_file), object_pairs_hook=OrderedDict)
    elif ext == '.yml':
        descs = yaml.load(open(test_file))
    else:
        raise ValueError('Unsupported test file format: {}'.format(ext))

    # Add path to the test names to asure uniqueness.
    tests = OrderedDict()
    for name, desc in descs.items():
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
        args = ['--sysstat']
        if test.code:
            args += ('--code', test.code)
        if test.input:
            args += ('--input', test.input)
        if test.gas:
            args += ('--gas', str(test.gas))
        return args

    def process_result(self, result):
        m = re.search('vm took (\d+(?:\.\d+)?)([mµ]?)s', result.out)
        value = float(m.group(1))
        unit = {'': 1, 'm': 1000, 'µ': 1000000}[m.group(2)]
        result.time = value / unit

        m = re.search('OUT: 0x([0-9a-f]*)', result.out)
        result.test_result.output = m.group(1)

        result.test_result.exception = (result.out.find('error: ') != -1)


class EthvmConnector(ToolConnector):
    def preprare_args(self, test):
        args = ['test', ]
        if test.code:
            args += ('--code', test.code)
        if test.input:
            args += ('--input', test.input)
        if test.gas:
            gas = test.gas + 50000  # FIXME: ethvm uses gas also on a tx.
            args += ('--gas', str(gas))
        return args

    def process_result(self, result):
        out = yaml.load(result.out)
        result.time = out.get('exec time')
        tres = result.test_result
        tres.exception = out.get('exception', False)
        tres.gas_used = out.get('gas used')
        tres.output = out.get('output')


class Tool(object):
    def __init__(self, name, path, params):
        self.name = name
        self.path = path
        self.params = params
        self.__conn = ToolConnector.get(self)

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


class Config(object):
    config_file = path.join(path.dirname(__file__), 'testeth.yml')

    def __init__(self):
        self.tools = []

    @classmethod
    def load(cls):
        if path.exists(cls.config_file):
            config = yaml.load(open(cls.config_file))
            if type(config) is not dict:
                return cls()
            if 'tools' in config:
                tools = []
                for name, desc in config['tools'].items():
                    tool = Tool(name, desc['path'], ())
                    if 'params' in desc:
                        tool.params = desc['params'].split()
                    tools.append(tool)
                tools.sort(key=lambda t: t.name)
        config = cls()
        config.tools = tools
        return config

    def save(self):
        tools = {}
        for tool in self.tools:
            desc = {'path': tool.path}
            if tool.params:
                desc['params'] = ' '.join(tool.params)
            tools[tool.name] = desc
        yaml.dump({'tools': tools}, open(self.config_file, 'w'),
                  default_flow_style=False)


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
    for tool in config.tools:
        print("> {} ...".format(tool.name))
        sys.stdout.flush()
        results = []
        for name, test in tests.items():
            res = tool.execute_test(test)
            results.append(res.value(test.expected))
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
    """ Manage the tools."""
    pass


@tool.command('list')
@click.pass_obj
def list_tools(config):
    """ List registered tools."""
    for tool in config.tools:
        print("{:<16}{} {}".format(tool.name, tool.path,
                                   ' '.join(tool.params)))


@tool.command('register')
@click.pass_obj
@click.argument('name')
@click.argument('path', type=click.Path())
@click.argument('params', nargs=-1, type=click.UNPROCESSED)
def register_tool(config, name, path, params):
    """ Register new tool.

        \b
        :param name:   Tool registration name.
        :param path:   Path to the tool exacutable.
        :param params: Additional params used to invoce the tool.
    """
    # TODO: Try get version number
    tool = Tool(name, path, params)
    config.tools.append(tool)
    config.save()


if __name__ == '__main__':
    testeth()
