# Capture decoder dependency review

The collector uses unmodified `simplejpeg==1.9.0` and `numpy==2.5.4`, installed
from hash-locked wheels. No upstream application source was copied. The decoder
is called with `strict=True`; original JPEG bytes are retained without re-encoding.
The direct and transitive package closure is in `uv.lock`, `requirements.lock`
and `supply-chain/licenses.json`. NumPy's Zlib license is a reviewed permissive
addition to the package metadata gate; GPL and unknown metadata remain rejected.

The reviewed Linux x86-64 CPython 3.14 wheels, upstream URLs, SHA-256 hashes and
all included license-file hashes are recorded in
[`supply-chain/capture-decoder`](../../supply-chain/capture-decoder/). Those exact
notices are retained there. This supplements package metadata, which does not
describe every bundled native runtime license:

| Component | Wheel license and notice |
| --- | --- |
| simplejpeg 1.9.0 | MIT, copyright Joachim Folz; bundled libjpeg-turbo 3.1.2 has BSD, IJG and Zlib notices in its wheel LICENSE. |
| NumPy 2.5.4 | BSD-3-Clause, 0BSD, MIT, Zlib and CC0-1.0 source components; individual notices retained. |
| NumPy's bundled OpenBLAS/LAPACK | BSD notices retained in the NumPy wheel LICENSE.txt. |
| NumPy's bundled libgfortran | GPL-3.0-or-later WITH GCC-exception-3.1; the complete GPL and exception text are retained. |
| NumPy's bundled libquadmath | LGPL-2.1-or-later; its copyright and license notice are retained. |

This software is based in part on the work of the Independent JPEG Group.

The native runtimes are binary dependencies with their own terms, not MIT
Weather source. The [GCC runtime exception](https://www.gnu.org/licenses/gcc-exception-3.1.en.html)
provides additional linking permissions for qualifying independent modules; it
does not remove the runtime's own GPL obligations. The
[LGPL shared-library terms](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html)
retain replacement and debugging rights. Weather does not modify these runtimes,
statically link them into its application, or restrict those rights.

The runtime image keeps the complete installed `*.dist-info/licenses` directories
and also copies this reviewed notice bundle to `/app/supply-chain/capture-decoder`.
Current images are used in Jason's private registry and estate; public Weather
responses contain images and JSON, not these executable packages. Before providing
an executable image or wheel bundle to other recipients, qualify the exact native
SBOM and provide the corresponding source and installation/relinking materials
required by its bundled runtime licenses. Package metadata alone is insufficient
for that distribution. A different platform wheel requires a fresh native-notice
review; this receipt covers the selected Linux x86-64 build only.

Capture's NumPy use is only the decoder array interface. Both
`OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1` are set in the runtime image. Real
JPEG decode and fake HTTP integration run through approved fleet CI, never on the
operator Mac. Wheel archive inspection and lock generation do not execute media.
