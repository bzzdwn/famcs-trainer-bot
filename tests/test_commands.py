from bot.commands import ADMIN_EXTRA, GUEST, STUDENT, commands_for


def names(cmds):
    return [c.command for c in cmds]


def test_guest_sees_start():
    assert names(commands_for(False, False)) == ["start", "help"]


def test_student_has_no_start():
    n = names(commands_for(True, False))
    assert "start" not in n and n[0] == "train" and {"blitz", "me", "stop", "help"} <= set(n)


def test_admin_sees_admin_commands():
    n = names(commands_for(True, True))
    assert n[:len(STUDENT)] == names(STUDENT)
    assert {"students", "rename", "kick", "broadcast", "export", "reload", "preview", "ocrdump"} <= set(n)
    assert "students" not in names(commands_for(True, False))


def test_descriptions_fit_telegram_limits():
    for c in GUEST + STUDENT + ADMIN_EXTRA:
        assert 1 <= len(c.command) <= 32 and c.command.islower()
        assert 1 <= len(c.description) <= 256
