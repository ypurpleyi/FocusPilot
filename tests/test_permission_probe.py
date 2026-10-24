from focuspilot.permission_probe import probe


def test_readonly_connection_does_not_isolate_same_user_child():
    """Exercise an independent write path, not just the business tool whitelist."""
    report = probe()
    assert report['readonly_connection_rejected_write'] is True
    assert report['write_tool_rejected'] is True
    assert report['sql_argument_rejected'] is True
    assert report['same_user_child_wrote_fixture'] is True
    assert report['same_user_child_read_control_file'] is True
    assert report['os_sandbox_verified'] is False
    assert report['model_called'] is False
    assert report['production_database_used'] is False
