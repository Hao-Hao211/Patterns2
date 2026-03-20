import os
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


class PromptManager:
    """Loads and renders prompt templates from .txt files."""

    def __init__(self, prompts_dir: Optional[str] = None):
        if prompts_dir is None:
            prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
        self.prompts_dir = prompts_dir
        self._cache = {}

    def load(self, filename: str, **kwargs) -> str:
        """Load a .txt prompt template and substitute {variables}."""
        if filename not in self._cache:
            filepath = os.path.join(self.prompts_dir, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                self._cache[filename] = f.read()
        template = self._cache[filename]
        return template.format(**kwargs)

    def clear_cache(self):
        """Clear the template cache (useful for hot-reloading prompts during development)."""
        self._cache.clear()

    def get_scientist_system(self, player_name: str, grid_size: int,
                              symbols_in_use: List[str]) -> str:
        """Build the scientist system message."""
        symbols_str = ", ".join(symbols_in_use)
        return self.load(
            "scientist_system.txt",
            player_name=player_name,
            grid_size=grid_size,
            grid_size_minus_1=grid_size - 1,
            symbols_str=symbols_str,
        )

    def get_scientist_turn(self, grid_size: int, symbols_in_use: List[str],
                            grid_display: str, turn_number: int,
                            observed_cells: int, total_cells: int,
                            unknown_cells: int) -> str:
        """Build the per-turn scientist prompt."""
        symbols_str = ", ".join(symbols_in_use)
        return self.load(
            "scientist_turn.txt",
            grid_size=grid_size,
            grid_size_minus_1=grid_size - 1,
            symbols_str=symbols_str,
            grid_display=grid_display,
            turn_number=turn_number,
            observed_cells=observed_cells,
            total_cells=total_cells,
            unknown_cells=unknown_cells,
        )

    def get_scientist_error_correction(self, grid_size: int, symbols_in_use: List[str],
                                        error_message: str,
                                        grid_display: str = "",
                                        observed_cells: int = 0,
                                        total_cells: int = 0,
                                        unknown_cells: int = 0,
                                        turn_number: int = 0) -> str:
        """Build the scientist error correction prompt."""
        symbols_str = ", ".join(symbols_in_use)
        self._cache.pop("scientist_error_correction.txt", None)  # Clear cache to pick up new template
        return self.load(
            "scientist_error_correction.txt",
            grid_size=grid_size,
            grid_size_minus_1=grid_size - 1,
            symbols_str=symbols_str,
            error_message=error_message,
            grid_display=grid_display,
            observed_cells=observed_cells,
            total_cells=total_cells,
            unknown_cells=unknown_cells,
            turn_number=turn_number,
        )

    def get_designer_system(self, grid_size: int, num_symbols: int,
                             available_symbols: List[str]) -> str:
        """Build the designer system prompt."""
        symbols_str = ", ".join(available_symbols)
        return self.load(
            "designer_system.txt",
            grid_size=grid_size,
            num_symbols=num_symbols,
            symbols_str=symbols_str,
        )

    def get_designer_user(self, user_prompt: Optional[str] = None) -> str:
        """Build the designer user prompt."""
        if user_prompt and user_prompt.strip():
            design_requirement = f" that follows the design requirement: {user_prompt.strip()}"
        else:
            design_requirement = ""
        return self.load(
            "designer_user.txt",
            design_requirement=design_requirement,
        )

    def get_evolving_context(self, game_history: list) -> str:
        """Build evolving context from previous game summaries for injection into system prompt."""
        history_text = ""
        for i, entry in enumerate(game_history):
            history_text += f"\nGame {i + 1}:\n"
            history_text += f"  Grid: {entry.get('grid_size', '?')}x{entry.get('grid_size', '?')}, Symbols: {entry.get('num_symbols', '?')}\n"
            history_text += f"  Score: {entry.get('score', '?')}\n"
            history_text += f"  Observations: {entry.get('num_observations', '?')} cells queried\n"

            # Include action log summary (last 5 actions for conciseness)
            action_log = entry.get('action_log', [])
            if action_log:
                recent_actions = action_log[-5:] if len(action_log) > 5 else action_log
                history_text += f"  Recent actions:\n"
                for action in recent_actions:
                    history_text += f"    - {action}\n"

            if entry.get('key_insight'):
                history_text += f"  Key insight: {entry['key_insight']}\n"
        return self.load("evolving.txt", game_history=history_text)

    def get_designer_error_correction(self, grid_size: int, num_symbols: int,
                                       available_symbols: List[str],
                                       error_message: str) -> str:
        """Build the designer error correction prompt (concise)."""
        symbols_str = ", ".join(available_symbols)
        self._cache.pop("designer_error_correction.txt", None)  # Clear cache to pick up new template
        return self.load(
            "designer_error_correction.txt",
            grid_size=grid_size,
            num_symbols=num_symbols,
            symbols_str=symbols_str,
            error_message=error_message,
        )


# Global instance
prompt_manager = PromptManager()
