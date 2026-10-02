from django.test import SimpleTestCase

from scripts.verify_database_transfer import VerificationFailure, assert_owned_target


class TransferCleanupSafetyTests(SimpleTestCase):
    def test_cleanup_rejects_application_database_even_when_marked_owned(self):
        name = "transfer_check_" + "a" * 32
        with self.assertRaises(VerificationFailure):
            assert_owned_target(name, {name}, name)

    def test_cleanup_rejects_unowned_targets_and_arbitrary_database_names(self):
        name = "transfer_check_" + "b" * 32
        for target, owned in ((name, set()), ("application", {"application"}),
                              ("postgres", {"postgres"}), ("template1", {"template1"})):
            with self.subTest(target=target):
                with self.assertRaises(VerificationFailure):
                    assert_owned_target(target, owned, "ci_database")
