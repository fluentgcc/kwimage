#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Update Requirments:
    # Requirements are broken down by type in the `requirements` folder, and
    # `requirments.txt` lists them all. Thus we autogenerate via:

    cat requirements/*.txt | sort -u | grep -o '^[^#]*' >  requirements.txt
"""
from os.path import exists
from os.path import join
from setuptools import find_packages
import sys
from os.path import dirname


try:
    import os
    val = os.environ.get('KWIMAGE_DISABLE_C_EXTENSIONS', '').lower()
    flag = val in {'true', 'on', 'yes', '1'}

    if '--universal' in sys.argv:
        flag = True

    if '--disable-c-extensions' in sys.argv:
        sys.argv.remove('--disable-c-extensions')
        flag = True

    if flag:
        # Hack to disable all compiled extensions
        from setuptools import setup
    else:
        from skbuild import setup
except ImportError:
    setup = None


repodir = dirname(__file__)


def parse_version(fpath):
    """
    Statically parse the version number from a python file
    """
    import ast
    if not exists(fpath):
        raise ValueError('fpath={!r} does not exist'.format(fpath))
    with open(fpath, 'r') as file_:
        sourcecode = file_.read()
    pt = ast.parse(sourcecode)
    class VersionVisitor(ast.NodeVisitor):
        def visit_Assign(self, node):
            for target in node.targets:
                if getattr(target, 'id', None) == '__version__':
                    self.version = node.value.s
    visitor = VersionVisitor()
    visitor.visit(pt)
    return visitor.version


def parse_description():
    """
    Parse the description in the README file

    CommandLine:
        pandoc --from=markdown --to=rst --output=README.rst README.md
        python -c "import setup; print(setup.parse_description())"
    """
    from os.path import dirname, join, exists
    readme_fpath = join(dirname(__file__), 'README.rst')
    # This breaks on pip install, so check that it exists.
    if exists(readme_fpath):
        with open(readme_fpath, 'r') as f:
            text = f.read()
        return text
    return ''


def parse_requirements(fname='requirements.txt', with_version=False):
    """
    Parse the package dependencies listed in a requirements file but strips
    specific versioning information.

    Args:
        fname (str): path to requirements file
        with_version (bool, default=False): if true include version specs

    Returns:
        List[str]: list of requirements items

    CommandLine:
        python -c "import setup; print(setup.parse_requirements())"
        python -c "import setup; print(chr(10).join(setup.parse_requirements(with_version=True)))"
    """
    from os.path import exists
    import re
    require_fpath = fname

    def parse_line(line):
        """
        Parse information from a line in a requirements text file
        """
        if line.startswith('-r '):
            # Allow specifying requirements in other files
            target = line.split(' ')[1]
            for info in parse_require_file(target):
                yield info
        else:
            info = {'line': line}
            if line.startswith('-e '):
                info['package'] = line.split('#egg=')[1]
            else:
                # Remove versioning from the package
                pat = '(' + '|'.join(['>=', '==', '>']) + ')'
                parts = re.split(pat, line, maxsplit=1)
                parts = [p.strip() for p in parts]

                info['package'] = parts[0]
                if len(parts) > 1:
                    op, rest = parts[1:]
                    if ';' in rest:
                        # Handle platform specific dependencies
                        # http://setuptools.readthedocs.io/en/latest/setuptools.html#declaring-platform-specific-dependencies
                        version, platform_deps = map(str.strip, rest.split(';'))
                        info['platform_deps'] = platform_deps
                    else:
                        version = rest  # NOQA
                    info['version'] = (op, version)
            yield info

    def parse_require_file(fpath):
        with open(fpath, 'r') as f:
            for line in f.readlines():
                line = line.strip()
                if line and not line.startswith('#'):
                    for info in parse_line(line):
                        yield info

    def gen_packages_items():
        if exists(require_fpath):
            for info in parse_require_file(require_fpath):
                parts = [info['package']]
                if with_version and 'version' in info:
                    parts.extend(info['version'])
                if not sys.version.startswith('3.4'):
                    # apparently package_deps are broken in 3.4
                    platform_deps = info.get('platform_deps')
                    if platform_deps is not None:
                        parts.append(';' + platform_deps)
                item = ''.join(parts)
                yield item

    packages = list(gen_packages_items())
    return packages


def clean():
    """
    __file__ = ub.truepath('~/code/kwimage/setup.py')
    """
    import ubelt as ub
    import os
    import glob

    modname = 'kwimage'
    repodir = dirname(os.path.realpath(__file__))

    toremove = []
    for root, dnames, fnames in os.walk(repodir):

        if os.path.basename(root) == modname + '.egg-info':
            toremove.append(root)
            del dnames[:]

        if os.path.basename(root) == '__pycache__':
            toremove.append(root)
            del dnames[:]

        if os.path.basename(root) == '_ext':
            # Remove torch extensions
            toremove.append(root)
            del dnames[:]

        if os.path.basename(root) == 'build':
            # Remove python c extensions
            if len(dnames) == 1 and dnames[0].startswith('temp.'):
                toremove.append(root)
                del dnames[:]

        # Remove simple pyx inplace extensions
        for fname in fnames:
            if fname.endswith('.pyc'):
                toremove.append(join(root, fname))
            if fname.endswith(('.so', '.c', '.o')):
                if fname.split('.')[0] + '.pyx' in fnames:
                    toremove.append(join(root, fname))

    def enqueue(d):
        if exists(d) and d not in toremove:
            toremove.append(d)

    enqueue(join(repodir, 'htmlcov'))
    enqueue(join(repodir, 'kwimage/algo/_nms_backend/cpu_nms.c'))
    enqueue(join(repodir, 'kwimage/algo/_nms_backend/cpu_nms.cpp'))
    enqueue(join(repodir, 'kwimage/algo/_nms_backend/gpu_nms.cpp'))
    enqueue(join(repodir, 'kwimage/structs/_boxes_backend/cython_boxes.c'))
    enqueue(join(repodir, 'kwimage/structs/_boxes_backend/cython_boxes.html'))
    for d in glob.glob(join(repodir, 'kwimage/algo/_nms_backend/*_nms.*so')):
        enqueue(d)

    for d in glob.glob(join(repodir, 'kwimage/structs/_boxes_backend/cython_boxes*.*so')):
        enqueue(d)

    for d in glob.glob(join(repodir, 'kwimage/structs/_mask_backend/cython_mask*.*so')):
        enqueue(d)

    enqueue(join(repodir, '_skbuild'))
    enqueue(join(repodir, '_cmake_test_compile'))
    enqueue(join(repodir, 'kwimage.egg-info'))
    enqueue(join(repodir, 'pip-wheel-metadata'))

    for dpath in toremove:
        ub.delete(dpath, verbose=1)


# Scikit-build extension module logic
compile_setup_kw = dict(
    # cmake_languages=('C', 'CXX', 'CUDA'),
    cmake_source_dir='.',
    # cmake_source_dir='kwimage',
)

# try:
#     import numpy as np
#     # Note: without this skbuild will fail with `pip install -e .`
#     # however, it will still work with `./setup.py develop`.
#     # Not sure why this is, could it be an skbuild bug?

#     # This should be something like:
#     # /home/joncrall/venv3.6/lib/python3.6/site-packages/numpy/core/include
#     # NOT:
#     # /tmp/pip-build-env-dt0w6ib0/overlay/lib/python3.6/site-packages/numpy/core/include
#     compile_setup_kw['cmake_args'] = [
#         '-D NumPy_INCLUDE_DIR:PATH=' + np.get_include(),
#         # '-D NPY_NO_DEPRECATED_API=TRUE',  # can cmake #define these?
#         # '-D NPY_1_7_API_VERSION=TRUE',
#     ]
# except ImportError:
#     pass


version = parse_version('kwimage/__init__.py')  # needs to be a global var for git tags

if __name__ == '__main__':
    if 'clean' in sys.argv:
        # hack
        clean()
        # sys.exit(0)
    if setup is None:
        raise ImportError('skbuild or setuptools failed to import')
    setup(
        name='kwimage',
        version=version,
        author='Jon Crall',
        author_email='jon.crall@kitware.com',
        long_description=parse_description(),
        long_description_content_type='text/x-rst',
        install_requires=parse_requirements('requirements/runtime.txt'),
        extras_require={
            'all': parse_requirements('requirements.txt'),
            'tests': parse_requirements('requirements/tests.txt'),
            'build': parse_requirements('requirements/build.txt'),
        },
        license='Apache 2',
        packages=find_packages(include='kwimage.*'),
        classifiers=[
            # List of classifiers available at:
            # https://pypi.python.org/pypi?%3Aaction=list_classifiers
            'Development Status :: 4 - Beta',
            # This should be interpreted as Apache License v2.0
            'License :: OSI Approved :: Apache Software License',
            # Supported Python versions
            'Programming Language :: Python :: 2.7',
            'Programming Language :: Python :: 3.5',
            'Programming Language :: Python :: 3.6',
            'Programming Language :: Python :: 3.7',
            'Programming Language :: Python :: 3.8',
        ],
        **compile_setup_kw
    )
