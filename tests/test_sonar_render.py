"""
Manual visual test for Sonar game renderer.

Run this script directly to generate test images:
    python -m tests.test_sonar_render

Images are saved to tests/output/ directory for visual inspection.
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.plugins.sonar import generate_sonar_image


def test_empty_grid():
    """Test rendering an empty sonar grid."""
    img_bytes = generate_sonar_image(
        grid_size=10,
        guesses={},
        found_chests=[],
        remaining_chests=[],
        game_over=False,
    )

    output_path = "tests/output/sonar_empty.png"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(img_bytes)
    print(f"✓ Generated: {output_path}")
    return img_bytes


def test_grid_with_guesses():
    """Test rendering a grid with various distance guesses."""
    guesses = {
        (0, 0): 1,  # Very close
        (0, 2): 2,  # Close
        (2, 2): 3,  # Medium close
        (3, 5): 5,  # Medium far
        (5, 5): 7,  # Far
        (7, 7): 9,  # Very far
        (1, 8): 4,  # Medium
    }

    img_bytes = generate_sonar_image(
        grid_size=10,
        guesses=guesses,
        found_chests=[],
        remaining_chests=[(4, 4), (8, 2)],  # Hidden chests
        game_over=False,
    )

    output_path = "tests/output/sonar_guesses.png"
    with open(output_path, "wb") as f:
        f.write(img_bytes)
    print(f"✓ Generated: {output_path}")
    return img_bytes


def test_grid_with_found_chests():
    """Test rendering a grid with found chests."""
    guesses = {
        (0, 0): 1,
        (0, 1): 0,  # Found chest
        (2, 2): 3,
        (4, 4): 0,  # Found chest
        (5, 5): 2,
    }

    found_chests = [(0, 1), (4, 4)]

    img_bytes = generate_sonar_image(
        grid_size=10,
        guesses=guesses,
        found_chests=found_chests,
        remaining_chests=[(8, 8)],  # One more hidden
        game_over=False,
    )

    output_path = "tests/output/sonar_found.png"
    with open(output_path, "wb") as f:
        f.write(img_bytes)
    print(f"✓ Generated: {output_path}")
    return img_bytes


def test_game_over_grid():
    """Test rendering a game over grid showing remaining chests."""
    guesses = {
        (0, 0): 1,
        (0, 1): 0,  # Found
        (2, 2): 3,
        (4, 4): 0,  # Found
        (5, 5): 2,
        (6, 6): 4,
    }

    found_chests = [(0, 1), (4, 4)]
    remaining_chests = [(8, 8), (2, 7)]  # These will be revealed

    img_bytes = generate_sonar_image(
        grid_size=10,
        guesses=guesses,
        found_chests=found_chests,
        remaining_chests=remaining_chests,
        game_over=True,
    )

    output_path = "tests/output/sonar_gameover.png"
    with open(output_path, "wb") as f:
        f.write(img_bytes)
    print(f"✓ Generated: {output_path}")
    return img_bytes


def test_full_gameplay_scenario():
    """Test a realistic gameplay scenario with multiple guesses and finds."""
    # Simulate a game where player found 2/3 chests
    guesses = {
        # Early guesses (getting close to first chest)
        (0, 0): 5,
        (3, 3): 2,
        (4, 2): 1,
        (4, 3): 0,  # Found first chest!
        # Searching for second chest
        (7, 7): 3,
        (8, 5): 2,
        (9, 4): 1,
        (9, 5): 0,  # Found second chest!
        # Random searches
        (5, 0): 4,
        (1, 9): 3,
    }

    found_chests = [(4, 3), (9, 5)]
    remaining_chests = [(1, 7)]  # Third chest not found yet

    img_bytes = generate_sonar_image(
        grid_size=10,
        guesses=guesses,
        found_chests=found_chests,
        remaining_chests=remaining_chests,
        game_over=False,
    )

    output_path = "tests/output/sonar_gameplay.png"
    with open(output_path, "wb") as f:
        f.write(img_bytes)
    print(f"✓ Generated: {output_path}")
    return img_bytes


def main():
    """Run all visual tests."""
    print("=" * 50)
    print("Sonar Renderer Visual Tests")
    print("=" * 50)
    print()

    # Ensure output directory exists
    os.makedirs("tests/output", exist_ok=True)

    print("Generating test images...")
    print()

    test_empty_grid()
    test_grid_with_guesses()
    test_grid_with_found_chests()
    test_game_over_grid()
    test_full_gameplay_scenario()

    print()
    print("=" * 50)
    print("All images generated in tests/output/")
    print("Open the PNG files to visually inspect the renders.")
    print("=" * 50)


if __name__ == "__main__":
    main()
