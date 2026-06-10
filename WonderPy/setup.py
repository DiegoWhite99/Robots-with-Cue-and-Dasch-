import setuptools

with open("README.md", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="WonderPy",
    version="0.3.0",
    author="Orion Elenzil",
    author_email="orion@makewonder.com",
    description="Python API for working with Wonder Workshop robots",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/playi/WonderPy",
    packages=setuptools.find_packages(),
    package_data={'WonderPy': ['lib/WonderWorkshop/osx/*.dylib']},
    classifiers=(
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "Framework :: Robot Framework",
        "Intended Audience :: Developers",
        "Intended Audience :: Education",
        "Intended Audience :: End Users/Desktop",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: MacOS",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
    ),
    keywords=['robots', 'dash', 'dot', 'cue', 'wonder workshop', 'robotics', 'sketchkit',],
    test_suite='test',
    install_requires=[
        'bleak>=2.1.1,<4',
        'mock>=5.2,<6',
        'svgpathtools>=1.7,<2',
    ],
)
