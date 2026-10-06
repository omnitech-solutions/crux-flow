"""Stack pack modules (ADR-0096 clause 12).

One module per pack, each exposing a detector and a probe registry. A pack
module MAY import from `..core` at module scope; only `core.py` may not
import from here, because `runtime/capture.py::_load_cell` file-loads it
outside the package. See `crux/arch/core.py` for why.
"""
