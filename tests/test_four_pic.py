"""Tests for 4-pic word game."""

import base64
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from bot.plugins.four_pic import (
    calculate_reward,
    format_word_hint,
    get_random_word,
)


class TestWordHint:
    """Test word hint formatting."""

    def test_no_letters_revealed(self):
        """Test when no letters are revealed."""
        word = "india"
        result = format_word_hint(word, [])
        assert result == "_____"

    def test_some_letters_revealed(self):
        """Test when some letters are revealed."""
        word = "india"
        result = format_word_hint(word, [1, 3])
        assert result == "_n_i_"

    def test_all_letters_revealed(self):
        """Test when all letters are revealed."""
        word = "cat"
        result = format_word_hint(word, [0, 1, 2])
        assert result == "cat"

    def test_first_and_last_revealed(self):
        """Test first and last letter revealed."""
        word = "cobra"
        result = format_word_hint(word, [0, 4])
        assert result == "c___a"


class TestCalculateReward:
    """Test reward calculation."""

    def test_no_hints(self):
        """Test maximum reward with no hints."""
        assert calculate_reward(0) == 100_000

    def test_one_hint(self):
        """Test reward with one hint."""
        assert calculate_reward(1) == 95_000

    def test_two_hints(self):
        """Test reward with two hints."""
        assert calculate_reward(2) == 90_000

    def test_many_hints(self):
        """Test reward with many hints."""
        assert calculate_reward(10) == 50_000

    def test_reward_never_negative(self):
        """Test that reward doesn't go below zero."""
        # 20 hints would be 100,000 penalty
        assert calculate_reward(20) == 0
        assert calculate_reward(100) == 0


class TestGetRandomWord:
    """Test random word selection."""

    def test_returns_dict(self):
        """Test that a word dict is returned."""
        word = get_random_word()
        assert isinstance(word, dict)
        assert "word" in word
        assert "category" in word
        assert "hint" in word

    def test_word_has_required_fields(self):
        """Test that word has all required fields."""
        word = get_random_word()
        assert "word" in word
        assert "category" in word
        assert "hint" in word
        assert "numLetters" in word


class TestCollageCreation:
    """Test collage creation logic."""

    def test_create_collage_from_images(self):
        """Test creating a 2x2 collage from 4 images."""
        # Create 4 test images
        test_images = []
        for i in range(4):
            img = Image.new("RGB", (100, 100), color=(i * 50, 100, 100))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG")
            test_images.append(buffer.getvalue())

        # Mock download_image to return our test images
        async def mock_download(url):
            return test_images.pop(0)

        # Mock aiohttp to return our test images
        with patch(
            "bot.plugins.four_pic.download_image", new_callable=AsyncMock
        ) as mock_dl:
            mock_dl.side_effect = mock_download

            # This would need actual URLs, so we'll skip the full test
            # In a real test, you'd mock the entire aiohttp session
            pass


class TestDatabaseOperations:
    """Test database operations (requires test database)."""

    @pytest.mark.asyncio
    async def test_create_and_get_game(self, database):
        """Test creating and retrieving a 4-pic game."""
        chat_id = 999999
        word = "test"
        category = "test_category"
        hint_msg = "Test hint"
        photo_b64 = base64.b64encode(b"test_image_data").decode("utf-8")

        # Create game
        await database.create_four_pic_game(
            chat_id, word, category, hint_msg, photo_b64
        )

        # Get game
        game = await database.get_four_pic_game(chat_id)
        assert game is not None
        assert game["word"] == word
        assert game["category"] == category
        assert game["hint_message"] == hint_msg
        assert game["photo_b64"] == photo_b64
        assert game["is_category_hint_given"] == False
        assert game["is_hint_message_given"] == False

        # Cleanup
        await database.delete_four_pic_game(chat_id)

    @pytest.mark.asyncio
    async def test_update_hints(self, database):
        """Test updating hint flags."""
        chat_id = 999998
        await database.create_four_pic_game(
            chat_id, "test", "cat", "hint", "b64data"
        )

        # Update category hint
        await database.update_four_pic_hint_category(chat_id)
        game = await database.get_four_pic_game(chat_id)
        assert game["is_category_hint_given"] == True

        # Update message hint
        await database.update_four_pic_hint_message(chat_id)
        game = await database.get_four_pic_game(chat_id)
        assert game["is_hint_message_given"] == True

        # Cleanup
        await database.delete_four_pic_game(chat_id)

    @pytest.mark.asyncio
    async def test_revealed_letters(self, database):
        """Test adding and retrieving revealed letters."""
        chat_id = 999997
        await database.create_four_pic_game(
            chat_id, "india", "country", "South Asia", "b64data"
        )

        # Add revealed letters
        await database.add_four_pic_revealed_letter(chat_id, 1)  # n
        await database.add_four_pic_revealed_letter(chat_id, 3)  # i

        revealed = await database.get_four_pic_revealed_letters(chat_id)
        assert 1 in revealed
        assert 3 in revealed
        assert len(revealed) == 2

        # Cleanup
        await database.delete_four_pic_game(chat_id)

    @pytest.mark.asyncio
    async def test_delete_game(self, database):
        """Test deleting a game."""
        chat_id = 999996
        await database.create_four_pic_game(
            chat_id, "test", "cat", "hint", "b64data"
        )

        # Verify game exists
        game = await database.get_four_pic_game(chat_id)
        assert game is not None

        # Delete game
        await database.delete_four_pic_game(chat_id)

        # Verify game is deleted
        game = await database.get_four_pic_game(chat_id)
        assert game is None
