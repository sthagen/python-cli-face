
from __future__ import print_function

import os

import pytest

from face import (Command,
                  Parser,
                  PosArgSpec,
                  ArgumentParseError,
                  CommandLineError)

from face.testing import TestClient

try:
    raw_input
except NameError:
    raw_input = input  # py3


def get_calc_cmd(as_parser=False):
    cmd = Command(None, 'calc')

    cmd.add(_add_cmd, name='add', posargs={'min_count': 2, 'parse_as': float})
    cmd.add(_add_two_ints, name='add_two_ints', posargs={'count': 2, 'parse_as': int, 'provides': 'ints'})
    cmd.add(_is_odd, name='is_odd', posargs={'count': 1, 'parse_as': int, 'provides': 'target_int'})
    cmd.add(_ask_halve, name='halve', posargs=False)

    if as_parser:
        cmd.__class__ = Parser

    return cmd


def _add_cmd(posargs_):
    "add numbers together"
    assert posargs_
    ret = sum(posargs_)
    print(ret)
    return ret


def _add_two_ints(ints):
    assert ints
    ret = sum(ints)
    # TODO: stderr
    return ret


def _ask_halve():
    val = float(raw_input('Enter a number: '))
    print()
    ret = val / float(os.getenv('CALC_TWO', 2))
    print(ret)
    return ret


def _is_odd(target_int):
    return bool(target_int % 2)


def test_calc_basic():
    prs = cmd = get_calc_cmd()

    res = prs.parse(['calc', 'add', '1.1', '2.2'])
    assert res

    with pytest.raises(ArgumentParseError):
        prs.parse(['calc', 'add-two-ints', 'not', 'numbers'])
    with pytest.raises(ArgumentParseError):
        prs.parse(['calc', 'add-two-ints', '1', '2', '3'])
    with pytest.raises(ArgumentParseError):
        prs.parse(['calc', 'add-two-ints', '1'])

    res = cmd.run(['calc', 'add-two-ints', '1', '2'])
    assert res == 3

    with pytest.raises(TypeError):
        prs.parse(['calc', 'is-odd', 3])  # fails bc 3 isn't a str

    res = cmd.run(['calc', 'is-odd', '3'])
    assert res == True
    res = cmd.run(['calc', 'is-odd', '4'])
    assert res == False


def test_calc_stream():
    cmd = get_calc_cmd()

    tc = TestClient(cmd)

    res = tc.invoke(['calc', 'add', '1', '2'])

    assert res.stdout.strip() == '3.0'

    res = tc.invoke(['calc', 'halve'], input='30')
    assert res.stdout.strip() == 'Enter a number: \n15.0'

    res = tc.invoke('calc halve', input='4', env={'CALC_TWO': '-2'})
    assert res.stdout.strip() == 'Enter a number: \n-2.0'
    assert not res.exception

    with pytest.raises(ZeroDivisionError):
        tc.invoke('calc halve', input='4', env={'CALC_TWO': '0'})

    return


def test_tc_exc():
    cmd = get_calc_cmd()
    tc_no_reraise = TestClient(cmd, reraise=False, echo_stdin=True)
    res = tc_no_reraise.invoke('calc halve', input='4', env={'CALC_TWO': '0'})
    assert res.exception
    assert res.stdout == 'Enter a number: 4\n'

    res = tc_no_reraise.invoke('calc halve nonexistentarg')
    assert type(res.exception) is CommandLineError

    # NB: expect to update these as error messaging improves
    assert str(res.exception) == "error: calc halve: unexpected positional arguments: ['nonexistentarg']"
    assert res.stderr.startswith("error: calc halve: unexpected positional arguments: ['nonexistentarg']")

    with pytest.raises(TypeError):
        tc_no_reraise.invoke('calc halve', input=object())

def test_tc_mixed(tmpdir):
    cmd = get_calc_cmd()
    tc_mixed = TestClient(cmd, reraise=False, mix_stderr=True, echo_stdin=True)
    res = tc_mixed.invoke('calc halve nonexistentarg', chdir=tmpdir)
    assert type(res.exception) is CommandLineError
    assert res.stdout.startswith("error: calc halve: unexpected positional arguments: ['nonexistentarg']")

    with pytest.raises(ValueError):
        res.stderr
