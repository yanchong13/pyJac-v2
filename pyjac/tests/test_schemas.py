"""
A unit tester that loads the schemas packaged with :mod:`pyJac` and validates
the given example specifications against them.
"""

# system
from os.path import isfile, join
from collections import OrderedDict

# external
import six
import cantera as ct
from nose.tools import assert_raises
from tempfile import NamedTemporaryFile

# internal
from ..libgen.libgen import build_type
from ..loopy_utils.loopy_utils import JacobianFormat
from ..utils import func_logger, enum_to_string, listify
from .test_utils import xfail
from . import script_dir as test_mech_dir
from .test_utils.get_test_matrix import load_models, load_platforms, load_tests
from ..examples import examples_dir
from ..schemas import schema_dir, __prefixify, build_and_validate
from ..core.exceptions import OverrideCollisionException, DuplicateTestException


@func_logger
def runschema(schema, source, should_fail=False, includes=[]):

    # add common
    includes.append('common_schema.yaml')

    # check source / schema / includes, and prepend prefixes
    def __check(file, fdir=schema_dir):
        assert isinstance(file, six.string_types), 'Schema file should be string'
        file = __prefixify(file, fdir)
        assert isfile(file), 'File {} not found.'.format(file)
        return file

    schema = __check(schema)
    source = __check(source, examples_dir)
    includes = [__check(inc) for inc in includes]

    # define inner tester
    @xfail(should_fail)
    def _internal(source, schema, includes):
        # make schema
        built = build_and_validate(schema, source, includes=includes)
        assert built is not None
        return built

    return _internal(source, schema, includes)


def test_test_platform_schema_specification():
    runschema('test_platform_schema.yaml', 'test_platforms.yaml')


def test_load_test_platforms():
    platforms = load_platforms(
        runschema('test_platform_schema.yaml', 'test_platforms.yaml'),
        raise_on_empty=True)
    platforms = [OrderedDict(p) for p in platforms]

    # amd
    amd = next(p for p in platforms if 'amd' in p['platform'].lower())
    assert amd['lang'] == 'opencl'
    assert amd['use_atomics'] is True

    def __fuzz_equal(arr):
        return arr == [2, 4, None] or arr == [2, 4]
    assert __fuzz_equal(amd['width'])
    assert __fuzz_equal(amd['depth'])
    assert amd['depth'] != amd['width']

    # openmp
    openmp = next(p for p in platforms if 'openmp' in p['platform'].lower())
    assert openmp['lang'] == 'c'
    assert openmp['width'] is None
    assert openmp['depth'] is None

    # nvidia
    openmp = next(p for p in platforms if 'nvidia' in p['platform'].lower())
    assert openmp['lang'] == 'opencl'
    assert openmp['width'] == [64, 128, 256]
    assert openmp['depth'] is None
    assert openmp['use_atomics'] is False

    # test empty platform w/ raise -> assert
    with assert_raises(Exception):
        load_platforms(None, raise_on_empty=True)

    # test empty platform
    platforms = load_platforms(None, langs=['c'], raise_on_empty=False)
    assert len(platforms) == 1
    openmp = OrderedDict(platforms[0])
    assert openmp['lang'] == 'c'
    assert openmp['platform'] == 'OpenMP'
    assert len(platforms[0]) == 2


def test_codegen_platform_schema_specification():
    runschema('codegen_platform.yaml', 'codegen_platform.yaml')


def test_load_codegen():
    from ..loopy_utils.loopy_utils import load_platform
    from pyopencl import Platform
    platform = load_platform(__prefixify(
            'codegen_platform.yaml', examples_dir))
    assert isinstance(platform.platform, Platform) or platform.platform == 'intel'
    assert platform.width == 4
    assert not platform.depth
    assert platform.use_atomics is False


def test_matrix_schema_specification():
    runschema('test_matrix_schema.yaml', 'test_matrix.yaml')


def __get_test_matrix(**kwargs):
    return build_and_validate('test_matrix_schema.yaml', __prefixify(
        'test_matrix.yaml', examples_dir),
        **kwargs)


def test_parse_models():
    models = load_models('', __get_test_matrix())

    # test the test mechanism
    assert 'TestMech' in models
    gas = ct.Solution(join(test_mech_dir, 'test.cti'))
    assert gas.n_species == models['TestMech']['ns']
    assert 'limits' in models['TestMech']

    def __test_limit(enumlist, limit):
        stypes = [enum_to_string(enum) for enum in listify(enumlist)]
        root = models['TestMech']['limits']
        for i, stype in enumerate(stypes):
            assert stype in root
            if i == len(stypes) - 1:
                assert root[stype] == limit
            else:
                root = root[stype]

    __test_limit(build_type.species_rates, 10000000)
    __test_limit([build_type.jacobian, JacobianFormat.sparse], 100000)
    __test_limit([build_type.jacobian, JacobianFormat.full], 1000)

    # test gri-mech
    assert 'CH4' in models
    gas = ct.Solution(models['CH4']['mech'])
    assert models['CH4']['ns'] == gas.n_species


def test_load_platforms_from_matrix():
    platforms = load_platforms(__get_test_matrix(allow_unknown=True),
                               raise_on_empty=True)
    platforms = [OrderedDict(p) for p in platforms]

    intel = next(p for p in platforms if 'intel' in p['platform'].lower())
    assert intel['lang'] == 'opencl'
    assert intel['use_atomics'] is False
    assert intel['width'] == [2, 4, 8, None]
    assert intel['depth'] is None

    openmp = next(p for p in platforms if 'openmp' in p['platform'].lower())
    assert openmp['lang'] == 'c'
    assert openmp['width'] is None
    assert openmp['depth'] is None

    # test empty platform w/ raise -> assert
    with assert_raises(Exception):
        load_platforms(None, raise_on_empty=True)

    # test empty platform
    platforms = load_platforms(None, langs=['c'], raise_on_empty=False)
    assert len(platforms) == 1
    openmp = OrderedDict(platforms[0])
    assert openmp['lang'] == 'c'
    assert openmp['platform'].lower() == 'openmp'
    assert len(platforms[0]) == 2


def test_duplicate_tests_fails():
    with NamedTemporaryFile('w', suffix='.yaml') as file:
        file.write("""
        model-list:
          - name: CH4
            path:
            mech: gri30.cti
        platform-list:
          - name: openmp
            lang: c
            vectype: [par]
        test-list:
          - type: performance
            eval-type: jacobian
          - type: performance
            eval-type: both
        """)
        file.seek(0)

        with assert_raises(DuplicateTestException):
            tests = build_and_validate('test_matrix_schema.yaml', file.name)
            load_tests(tests, file.name)

    with NamedTemporaryFile('w', suffix='.yaml') as file:
        file.write("""
        model-list:
          - name: CH4
            path:
            mech: gri30.cti
        platform-list:
          - name: openmp
            lang: c
            vectype: [par]
        test-list:
          - type: performance
            eval-type: jacobian
            sparse:
                num_cores: [1]
            finite_difference:
                num_cores: [1]
        """)
        file.seek(0)

        with assert_raises(OverrideCollisionException):
            tests = build_and_validate('test_matrix_schema.yaml', file.name)
            load_tests(tests, file.name)


def test_load_tests():
    # load tests doesn't do any processing other than collision / duplicate
    # checking, hence we just check we get the right number of tests
    tests = load_tests(__get_test_matrix(), 'test_matrix_schema.yaml')
    assert len(tests) == 3
