import sys
from pathlib import Path

import pytest

from beadsflow.infra.zellij import Zellij, ZellijError


@pytest.mark.parametrize("stream", [1, 2])
@pytest.mark.parametrize("hang", [False, True])
def test_inventory_overflow_is_a_safe_failure(tmp_path: Path, stream: int, hang: bool) -> None:
    executable = tmp_path / "zellij"
    executable.write_text(
        f"#!{sys.executable}\nimport os, time\n"
        f"for _ in range(64): os.write({stream}, b'synthetic-private' * 65536)\n"
        + ("time.sleep(30)\n" if hang else "")
    )
    executable.chmod(0o700)
    with pytest.raises(ZellijError, match="output exceeded") as error:
        Zellij(executable=str(executable)).has_session(session_name="sess")
    assert "synthetic-private" not in str(error.value)
