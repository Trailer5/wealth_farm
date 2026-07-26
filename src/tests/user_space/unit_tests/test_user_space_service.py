import unittest

from src.user_space import SQLiteUserSpaceRepository, UserSpaceService


class UserSpaceServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = UserSpaceService(SQLiteUserSpaceRepository(":memory:"))

    def test_default_user_is_created_once(self):
        first = self.service.default_context()
        second_service = UserSpaceService(self.service.repository)
        second = second_service.default_context()

        self.assertEqual(first.user_id, "user:default")
        self.assertEqual(second.user_id, first.user_id)
        self.assertEqual(len(self.service.list_users()), 1)

    def test_users_can_be_created_and_selected(self):
        alice = self.service.create_user("Alice")
        bob = self.service.create_user("Bob")

        self.assertNotEqual(alice.user_id, bob.user_id)
        self.assertEqual(
            self.service.context_for_display_name("alice").user_id,
            alice.user_id,
        )
        self.assertEqual(
            self.service.context_for_user(bob.user_id).user_id,
            bob.user_id,
        )

    def test_duplicate_display_name_is_rejected(self):
        self.service.create_user("Alice")

        with self.assertRaises(ValueError):
            self.service.create_user("alice")

    def test_unknown_user_context_is_rejected(self):
        with self.assertRaises(KeyError):
            self.service.context_for_user("user:missing")


if __name__ == "__main__":
    unittest.main()
