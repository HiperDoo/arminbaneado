import os

import config


def test_env_file_is_loaded_without_overriding_real_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comentario\nIP_SALT=desde-archivo\nNONCE_SECRET='n'\nTURNSTILE_SECRET=t\n")
    for k in ("IP_SALT", "NONCE_SECRET", "TURNSTILE_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("IP_SALT", "del-entorno")
    config._load_env_file(env_file)
    assert os.environ["IP_SALT"] == "del-entorno"
    assert os.environ["NONCE_SECRET"] == "n"
    assert os.environ["TURNSTILE_SECRET"] == "t"
