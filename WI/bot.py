import asyncio
import os
import random
from typing import Dict, List, Optional, Set
import discord
from discord import Intents, Interaction, SelectOption, Color, ButtonStyle
from discord.ext import commands
from dataclasses import dataclass
from datetime import datetime, timedelta
import traceback
import json
import re
from better_profanity import profanity

# Configure intents
intents = Intents.default()
intents.message_content = True
intents.members = True


# Bot initialization
def init_bot():
    return commands.Bot(command_prefix="/", intents=intents)


# Create bot instance
bot = init_bot()


class ErrorHandler:
    @staticmethod
    async def handle_command_error(interaction: Interaction, error: Exception):
        error_message = "An unexpected error occurred! Please try again later."
        if isinstance(error, commands.MissingPermissions):
            error_message = "You don't have permission to use this command!"
        elif isinstance(error, commands.CommandOnCooldown):
            error_message = f"Please wait {error.retry_after:.1f}s before using this command again."

        try:
            await interaction.response.send_message(
                error_message, ephemeral=True
            )
        except discord.InteractionResponded:
            await interaction.followup.send(error_message, ephemeral=True)

        print(f"Error in {interaction.command.name}: {str(error)}")
        traceback.print_exception(type(error), error, error.__traceback__)


@dataclass
class ServerSettings:
    min_players: int = 1
    max_players: int = 10
    rounds: int = 3
    description_timeout: int = 60
    vote_timeout: int = 120
    max_missed_rounds: int = 2
    multiple_imposters: bool = False
    imposter_ratio: float = 0.25  # 25% of players will be imposters in larger games


class GameState:
    def __init__(self):
        self.joined_users: List[int] = []
        self.game_started: bool = False
        self.imposters: Set[int] = set()
        self.current_word: Optional[str] = None
        self.description_phase_started: bool = False
        self.user_descriptions: Dict[int, List[str]] = {}
        self.votes: Dict[int, int] = {}
        self.message_id: Optional[int] = None
        self.missed_rounds: Dict[int, int] = {}
        self.round_number: int = 0
        self.start_time: Optional[datetime] = None
        self.vote_message_sent: datetime = None
        self.voted_users: Set[int] = set()
        self.channel_id: Optional[int] = None
        self.vote_task: Optional[asyncio.Task] = None
        self.start_button_message: Optional[discord.Message] = None
        self.kicked_users: Set[int] = set()
        self.left_users: Set[int] = set()

    def remove_player(self, player_id: int) -> None:
        """Completely remove a player from all game state"""
        if player_id in self.joined_users:
            self.joined_users.remove(player_id)
        if player_id in self.imposters:
            self.imposters.remove(player_id)
        if player_id in self.user_descriptions:
            del self.user_descriptions[player_id]
        if player_id in self.votes:
            del self.votes[player_id]
        if player_id in self.missed_rounds:
            del self.missed_rounds[player_id]
        if player_id in self.voted_users:
            self.voted_users.remove(player_id)
        # Remove any votes cast for this player
        self.votes = {k: v for k, v in self.votes.items() if v != player_id}

    def reset(self):
        if self.vote_task:
            self.vote_task.cancel()
        self.__init__()


class ServerConfig:
    def __init__(self, config_file: str = "server_config.json"):
        self.config_file = config_file
        self.settings: Dict[str, ServerSettings] = {}
        self._ensure_config_file()
        self.load_config()

    def _ensure_config_file(self):
        """Ensure the config file exists and is valid JSON"""
        if not os.path.exists(self.config_file):
            with open(self.config_file, "w") as f:
                json.dump({}, f)
        else:
            try:
                with open(self.config_file, "r") as f:
                    json.load(f)
            except json.JSONDecodeError:
                # Backup corrupted file and create new one
                backup_file = f"{self.config_file}.backup"
                os.rename(self.config_file, backup_file)
                with open(self.config_file, "w") as f:
                    json.dump({}, f)

    def load_config(self) -> None:
        """Load server settings from config file with error handling"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "r") as f:
                    data = json.load(f)
                    for server_id, settings in data.items():
                        try:
                            self.settings[server_id] = ServerSettings(**settings)
                        except TypeError as e:
                            print(f"Error loading settings for server {server_id}: {e}")
                            # Use default settings for invalid configurations
                            self.settings[server_id] = ServerSettings()
        except Exception as e:
            print(f"Error loading config file: {e}")
            # Use empty settings if config file can't be loaded
            self.settings = {}

    def save_config(self) -> bool:
        """Save server settings to config file with error handling"""
        try:
            data = {
                server_id: {
                    "min_players": settings.min_players,
                    "max_players": settings.max_players,
                    "rounds": settings.rounds,
                    "description_timeout": settings.description_timeout,
                    "vote_timeout": settings.vote_timeout,
                    "max_missed_rounds": settings.max_missed_rounds,
                    "multiple_imposters": settings.multiple_imposters,
                    "imposter_ratio": settings.imposter_ratio,
                }
                for server_id, settings in self.settings.items()
            }
            
            # Write to temporary file first
            temp_file = f"{self.config_file}.temp"
            with open(temp_file, "w") as f:
                json.dump(data, f, indent=4)
            
            # Replace original file with temporary file
            os.replace(temp_file, self.config_file)
            return True
        except Exception as e:
            print(f"Error saving config file: {e}")
            return False

    def get_settings(self, server_id: str) -> ServerSettings:
        """Get settings for a specific server, creating default if none exist"""
        if not server_id:
            return ServerSettings()
        
        if server_id not in self.settings:
            self.settings[server_id] = ServerSettings()
            self.save_config()
        return self.settings[server_id]

    def update_server_settings(
        self,
        server_id: str,
        **kwargs
    ) -> tuple[bool, str]:
        """Update settings for a specific server with validation"""
        try:
            settings = self.get_settings(server_id)
            
            # Validate numeric settings
            if "min_players" in kwargs and "max_players" in kwargs:
                if kwargs["min_players"] > kwargs["max_players"]:
                    return False, "Minimum players cannot be greater than maximum players"
            elif "min_players" in kwargs and kwargs["min_players"] > settings.max_players:
                return False, "Minimum players cannot be greater than current maximum players"
            elif "max_players" in kwargs and kwargs["max_players"] < settings.min_players:
                return False, "Maximum players cannot be less than current minimum players"

            # Validate timeouts
            if "description_timeout" in kwargs and kwargs["description_timeout"] < 10:
                return False, "Description timeout must be at least 10 seconds"
            if "vote_timeout" in kwargs and kwargs["vote_timeout"] < 10:
                return False, "Vote timeout must be at least 10 seconds"

            # Update settings
            for key, value in kwargs.items():
                if hasattr(settings, key):
                    setattr(settings, key, value)

            # Save changes
            if not self.save_config():
                return False, "Failed to save settings"

            return True, "Settings updated successfully"
        except Exception as e:
            return False, f"Error updating settings: {e}"

class WordManager:
    def __init__(
        self,
        words_file: str = "nouns.txt",
        used_words_file: str = "used_words.txt",
    ):
        self.words_file = words_file
        self.used_words_file = used_words_file
        self.request_cooldowns: Dict[int, datetime] = {}
        self._ensure_files_exist()
        profanity.load_censor_words()

    def _ensure_files_exist(self):
        for file in [self.words_file, self.used_words_file]:
            if not os.path.exists(file):
                with open(file, "w") as f:
                    f.write("")

    def get_random_word(self) -> str:
        with open(self.words_file, "r") as f:
            words = set(f.read().splitlines())

        with open(self.used_words_file, "r") as f:
            used_words = set(f.read().splitlines())

        available_words = words - used_words
        if not available_words:
            available_words = words
            with open(self.used_words_file, "w") as f:
                f.write("")

        word = random.choice(list(available_words))
        with open(self.used_words_file, "a") as f:
            f.write(f"{word}\n")

        return word

    def is_appropriate_word(self, word: str) -> bool:
        # Check for profanity
        if profanity.contains_profanity(word):
            return False

        # Check for valid word format (letters only, no numbers or special characters)
        if not re.match(r"^[a-zA-Z]+$", word):
            return False

        return True

    def check_cooldown(self, user_id: int) -> bool:
        if user_id in self.request_cooldowns:
            last_request = self.request_cooldowns[user_id]
            if datetime.now() - last_request < timedelta(minutes=5):
                return False
        return True

    async def add_word(self, word: str, user_id: int) -> tuple[bool, str]:
        if not self.check_cooldown(user_id):
            return False, "Please wait 5 minutes between word requests."

        word = word.strip().lower()

        if not self.is_appropriate_word(word):
            return (
                False,
                "This word is not appropriate or contains invalid characters.",
            )

        with open(self.words_file, "r") as f:
            existing_words = set(f.read().splitlines())

        if word in existing_words:
            return False, "This word already exists in the word list."

        with open(self.words_file, "a") as f:
            f.write(f"{word}\n")

        self.request_cooldowns[user_id] = datetime.now()
        return True, f"Successfully added '{word}' to the word list!"


class PlayAgainView(discord.ui.View):
    def __init__(self, game_manager, channel_id):
        super().__init__()
        self.game_manager = game_manager
        self.channel_id = channel_id

    @discord.ui.button(label="Play Again", style=ButtonStyle.green)
    async def play_again(
        self, interaction: Interaction, button: discord.ui.Button
    ):
        # Create a new game
        await play(interaction)


class VoteKickView(discord.ui.View):
    def __init__(self, game: GameState, target_id: int):
        super().__init__()
        self.game = game
        self.target_id = target_id
        self.votes = set()
        self.required_votes = max(2, len(game.joined_users) // 2)

    @discord.ui.button(label="Vote to Kick", style=ButtonStyle.red)
    async def vote_kick(
        self, interaction: Interaction, button: discord.ui.Button
    ):
        if interaction.user.id not in self.game.joined_users:
            await interaction.response.send_message(
                "You're not in the game!", ephemeral=True
            )
            return

        if interaction.user.id in self.votes:
            await interaction.response.send_message(
                "You've already voted!", ephemeral=True
            )
            return

        if self.target_id in self.game.kicked_users:
            await interaction.response.send_message(
                "This player has already been kicked!", ephemeral=True
            )
            return

        self.votes.add(interaction.user.id)
        remaining_votes = self.required_votes - len(self.votes)

        if remaining_votes <= 0:
            # Remove player completely from game state
            self.game.remove_player(self.target_id)
            self.game.kicked_users.add(self.target_id)

            # Disable the button after successful kick
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)

            await interaction.response.send_message(
                f"<@{self.target_id}> has been kicked from the game."
            )

            # Check if game should end due to insufficient players
            settings = server_config.get_settings(str(interaction.guild_id))
            if len(self.game.joined_users) < settings.min_players:
                await interaction.channel.send(
                    "Not enough players remaining. Game ending."
                )
                game_manager.end_game(interaction.channel.id)
            self.stop()
        else:
            await interaction.response.send_message(
                f"Vote recorded. {remaining_votes} more votes needed to kick.",
                ephemeral=True,
            )


class VotingDropdown(discord.ui.Select):
    def __init__(self, game: GameState, options: List[SelectOption]):
        super().__init__(
            placeholder="Vote for the Imposter",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.game = game

    async def callback(self, interaction: Interaction):
        if interaction.user.id not in self.game.joined_users:
            await interaction.response.send_message(
                "You're not part of this game!", ephemeral=True
            )
            return

        if interaction.user.id in self.game.votes:
            await interaction.response.send_message(
                "You've already voted!", ephemeral=True
            )
            return

        voted_user_id = int(self.values[0])
        self.game.votes[interaction.user.id] = voted_user_id
        self.game.voted_users.add(interaction.user.id)
        user = await interaction.client.fetch_user(voted_user_id)
        await interaction.response.send_message(
            f"You voted for {user.name}.", ephemeral=True
        )

        # Check if everyone has voted and automatically tally
        if len(self.game.votes) == len(self.game.joined_users):
            channel = await bot.fetch_channel(self.game.channel_id)
            await tally_votes(channel, self.game)


class StartGameButton(discord.ui.Button):
    def __init__(self, game: GameState):
        super().__init__(
            style=ButtonStyle.green, label="Start Game", disabled=True
        )
        self.game = game

    async def callback(self, interaction: Interaction):
        settings = server_config.get_settings(str(interaction.guild.id))

        if len(self.game.joined_users) < settings.min_players:
            await interaction.response.send_message(
                f"Need at least {settings.min_players} players to start!",
                ephemeral=True,
            )
            return

        await start_game(interaction, self.game)


class GameView(discord.ui.View):
    def __init__(self, game: GameState):
        super().__init__()
        self.game = game
        self.start_button = StartGameButton(game)
        self.add_item(self.start_button)

    @discord.ui.button(label="Join Game", style=ButtonStyle.green)
    async def join_button(
        self, interaction: Interaction, button: discord.ui.Button
    ):
        settings = server_config.get_settings(str(interaction.guild_id))

        if len(self.game.joined_users) >= settings.max_players:
            await interaction.response.send_message(
                f"Game is full! Maximum {settings.max_players} players allowed.",
                ephemeral=True,
            )
            return

        if interaction.user.id in self.game.joined_users:
            await interaction.response.send_message(
                "You've already joined!", ephemeral=True
            )
            return

        self.game.joined_users.append(interaction.user.id)
        embed = interaction.message.embeds[0]
        embed.description = f"Players joined: {len(self.game.joined_users)}/{settings.max_players}"

        # Enable start button if minimum players reached
        if len(self.game.joined_users) >= settings.min_players:
            self.start_button.disabled = False

        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message(
            "You've joined the game!", ephemeral=True
        )


class GameManager:
    def __init__(self):
        self.games: Dict[int, GameState] = {}
        self.word_manager = WordManager()
        self.used_channels: Set[int] = set()

    def get_game(self, channel_id: int) -> Optional[GameState]:
        return self.games.get(channel_id)

    def create_game(self, channel_id: int) -> GameState:
        game = GameState()
        game.channel_id = channel_id
        self.games[channel_id] = game
        self.used_channels.add(channel_id)
        return game

    def end_game(self, channel_id: int):
        if channel_id in self.games:
            self.games[channel_id].reset()
            del self.games[channel_id]

    def can_create_game(self, channel_id: int) -> bool:
        return channel_id not in self.used_channels


game_manager = GameManager()
server_config = ServerConfig()


async def auto_tally_votes(game: GameState, channel):
    try:
        await asyncio.sleep(
            server_config.get_settings(str(channel.guild.id)).vote_timeout
        )
        if game.game_started and len(game.votes) > 0:
            await tally_votes(channel, game)
    except asyncio.CancelledError:
        pass


async def tally_votes(channel, game: GameState):
    if len(game.votes) == 0:
        await channel.send("No votes were cast!")
        game_manager.end_game(channel.id)
        return

    # Count votes
    vote_counts = {}
    for voted_id in game.votes.values():
        vote_counts[voted_id] = vote_counts.get(voted_id, 0) + 1

    # Find player(s) with most votes
    max_votes = max(vote_counts.values())
    voted_players = [
        pid for pid, votes in vote_counts.items() if votes == max_votes
    ]

    # Create results embed
    embed = discord.Embed(
        title="📊 Voting Results", color=Color.blue(), timestamp=datetime.now()
    )

    # Add voting breakdown
    voting_breakdown = []
    for user_id, votes in vote_counts.items():
        user = await bot.fetch_user(user_id)
        voting_breakdown.append(f"{user.name}: {votes} votes")
    embed.add_field(
        name="Votes Received",
        value="\n".join(voting_breakdown) or "No votes",
        inline=False,
    )

    # Game outcome
    if len(voted_players) == 1 and voted_players[0] in game.imposters:
        embed.add_field(
            name="🎉 Players Win!",
            value="They caught the imposter!",
            inline=False,
        )
    else:
        embed.add_field(
            name="👻 Imposters Win!", value="They weren't caught!", inline=False
        )
        imposters = [await bot.fetch_user(imp_id) for imp_id in game.imposters]
        embed.add_field(
            name="The Imposters Were",
            value=", ".join(imp.name for imp in imposters),
            inline=False,
        )
        embed.add_field(
            name="The Word Was", value=game.current_word, inline=False
        )

    # Game statistics
    game_duration = datetime.now() - game.start_time
    stats = [
        f"Duration: {game_duration.seconds // 60} minutes",
        f"Players: {len(game.joined_users)}",
        f"Descriptions: {sum(len(desc) for desc in game.user_descriptions.values())}",
    ]
    embed.add_field(
        name="📈 Game Statistics", value="\n".join(stats), inline=False
    )

    # Send results and prompt for new game
    await channel.send(
        embed=embed, view=PlayAgainView(game_manager, channel.id)
    )
    game_manager.end_game(channel.id)


@bot.tree.command(name="play", description="Start a new game of Word Imposter")
@commands.cooldown(1, 30, commands.BucketType.channel)
async def play(interaction: Interaction):
    if not interaction.guild or not interaction.channel:
        await interaction.response.send_message(
            "This command can only be used in a server channel.", ephemeral=True
        )
        return

    if not game_manager.can_create_game(interaction.channel.id):
        await interaction.response.send_message(
            "A game has already been started in this channel!", ephemeral=True
        )
        return

    game = game_manager.create_game(interaction.channel.id)
    settings = server_config.get_settings(str(interaction.guild_id))

    embed = discord.Embed(
        title="Word Imposter",
        description=f"Players joined: 0/{settings.max_players}",
        color=Color.green(),
    )
    embed.add_field(
        name="How to Play",
        value="Join the game and try to identify the imposter who doesn't know the secret word!",
    )

    view = GameView(game)
    await interaction.response.send_message(embed=embed, view=view)
    message = await interaction.original_response()
    game.message_id = message.id


async def start_game(interaction: Interaction, game: GameState):
    settings = server_config.get_settings(str(interaction.guild.id))

    game.game_started = True
    game.start_time = datetime.now()

    # Calculate number of imposters based on player count and settings
    if settings.multiple_imposters and len(game.joined_users) >= 6:
        num_imposters = max(
            1, int(len(game.joined_users) * settings.imposter_ratio)
        )
    else:
        num_imposters = 1

    # Select imposters
    imposters = random.sample(game.joined_users, num_imposters)
    game.imposters = set(imposters)
    game.current_word = game_manager.word_manager.get_random_word()

    # Send word information to players
    for user_id in game.joined_users:
        try:
            user = await bot.fetch_user(user_id)
            message = (
                "You are an imposter! Try to blend in!"
                if user_id in game.imposters
                else f"The word is: {game.current_word}"
            )
            await user.send(message)
        except discord.DiscordException as e:
            print(f"Failed to send message to user {user_id}: {e}")

    await interaction.response.send_message(
        "Game has started! Description phase beginning..."
    )

    # Automatically start description phase
    await asyncio.sleep(2)  # Give players time to read their roles
    await start_description_phase(interaction, game)


async def start_description_phase(interaction: Interaction, game: GameState):
    settings = server_config.get_settings(str(interaction.guild.id))

    game.description_phase_started = True
    await interaction.channel.send("Description phase starting!")

    # Calculate dynamic timeout based on player count
    player_count = len(game.joined_users)
    base_timeout = settings.description_timeout
    timeout_adjustment = max(0, (player_count - 5) * 10)
    adjusted_timeout = base_timeout + timeout_adjustment

    for round_num in range(settings.rounds):
        game.round_number = round_num + 1
        await interaction.channel.send(
            f"Round {game.round_number}/{settings.rounds}"
        )

        players = game.joined_users.copy()
        random.shuffle(players)

        for player_id in players:
            if player_id not in game.joined_users:
                continue

            player = await bot.fetch_user(player_id)
            await interaction.channel.send(
                f"{player.mention}'s turn to describe!"
            )

            try:

                def check(m):
                    return (
                        m.author.id == player_id
                        and m.channel.id == interaction.channel.id
                    )

                msg = await bot.wait_for(
                    "message", timeout=adjusted_timeout, check=check
                )

                if player_id not in game.user_descriptions:
                    game.user_descriptions[player_id] = []
                game.user_descriptions[player_id].append(msg.content)

            except asyncio.TimeoutError:
                await interaction.channel.send(
                    f"{player.mention} took too long!"
                )
                game.missed_rounds[player_id] = (
                    game.missed_rounds.get(player_id, 0) + 1
                )

                if game.missed_rounds[player_id] >= settings.max_missed_rounds:
                    game.joined_users.remove(player_id)
                    await interaction.channel.send(
                        f"{player.mention} has been removed for inactivity!"
                    )

    await interaction.channel.send(
        "Description phase complete! Use /vote to start voting!"
    )


@bot.tree.command(name="vote", description="Start the voting phase")
@commands.cooldown(1, 5, commands.BucketType.channel)
async def vote(interaction: Interaction):
    game = game_manager.get_game(interaction.channel.id)
    if not game or not game.game_started:
        await interaction.response.send_message(
            "No active game found!", ephemeral=True
        )
        return

    if not game.description_phase_started:
        await interaction.response.send_message(
            "Complete the description phase first!", ephemeral=True
        )
        return

    options = []
    for user_id in game.joined_users:
        user = await bot.fetch_user(user_id)
        options.append(SelectOption(label=user.name, value=str(user_id)))

    game.vote_message_sent = datetime.now()

    for user_id in game.joined_users:
        try:
            user = await bot.fetch_user(user_id)
            view = discord.ui.View()
            view.add_item(VotingDropdown(game, options))
            await user.send(
                "Vote for who you think is the imposter:", view=view
            )
        except discord.DiscordException as e:
            print(f"Failed to send voting message to {user_id}: {e}")

    await interaction.response.send_message(
        "Voting has started! Results will be shown when everyone has voted."
    )


@bot.tree.command(
    name="recall", description="Show all descriptions given during the game"
)
@commands.cooldown(1, 5, commands.BucketType.channel)
async def recall(interaction: Interaction):
    game = game_manager.get_game(interaction.channel.id)
    if not game or not game.description_phase_started:
        await interaction.response.send_message(
            "No active game with descriptions found!", ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🗣️ Game Descriptions",
        color=Color.blue(),
        timestamp=datetime.now(),
    )

    for user_id, descriptions in game.user_descriptions.items():
        user = await bot.fetch_user(user_id)
        desc_text = "\n".join(
            [f"Round {i+1}: {desc}" for i, desc in enumerate(descriptions)]
        )
        embed.add_field(
            name=f"{user.name}'s Descriptions",
            value=desc_text or "No descriptions",
            inline=False,
        )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="rules", description="Show the rules and how to play Word Imposter"
)
@commands.cooldown(1, 5, commands.BucketType.channel)
async def rules(interaction: Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used in a server.", ephemeral=True
        )
        return

    settings = server_config.get_settings(str(interaction.guild.id))

    embed = discord.Embed(
        title="📖 How to Play Word Imposter",
        color=Color.blue(),
        description="Word Imposter is a social deduction game where players try to identify who doesn't know the secret word!",
    )

    # Game Setup
    embed.add_field(
        name="🎮 Game Setup",
        value=(
            f"• {settings.min_players}-{settings.max_players} players can join\n"
            f"• {'One imposter' if not settings.multiple_imposters else 'Multiple imposters'} will be chosen\n"
            f"• Everyone except the imposter(s) gets to see the secret word\n"
            f"• The game lasts {settings.rounds} rounds"
        ),
        inline=False,
    )

    # How to Play
    embed.add_field(
        name="🎯 How to Play",
        value=(
            "1. Join the game with the button when someone uses `/play`\n"
            "2. Once enough players join, anyone can start with `/start`\n"
            "3. Check your DMs to see if you're an imposter!\n"
            "4. Use `/describe` to begin the description phase"
        ),
        inline=False,
    )

    # Description Phase
    embed.add_field(
        name="📝 Description Phase",
        value=(
            f"• Each player has {settings.description_timeout} seconds to describe the word\n"
            "• If you're not the imposter, describe the word without saying it\n"
            "• If you are the imposter, try to blend in!\n"
            f"• Missing {settings.max_missed_rounds} rounds will remove you from the game\n"
            "• Use `/recall` to see all descriptions"
        ),
        inline=False,
    )

    # Voting Phase
    embed.add_field(
        name="🗳️ Voting Phase",
        value=(
            "• After descriptions, use `/vote` to start voting\n"
            f"• Players have {settings.vote_timeout} seconds to vote via DM\n"
            "• Vote for who you think is the imposter\n"
            "• Use `/tally` to see results early if everyone voted"
        ),
        inline=False,
    )

    # Winning
    embed.add_field(
        name="🏆 Winning",
        value=(
            "**Regular Players Win If:**\n"
            "**Imposter Wins If:**\n"
            "• They avoid being caught\n"
            "• Players vote out a regular player"
        ),
        inline=False,
    )

    # Additional Commands
    embed.add_field(
        name="⚡ Helpful Commands",
        value=(
            "`/status` - Check game progress\n"
            "`/votekick` - Start a vote to remove inactive players\n"
            "`/request` - Suggest new words for the game"
        ),
        inline=False,
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="status", description="Show current game status")
@commands.cooldown(1, 5, commands.BucketType.channel)
async def status(interaction: Interaction):
    game = game_manager.get_game(interaction.channel.id)
    settings = server_config.get_settings(str(interaction.guild.id))

    if not game:
        await interaction.response.send_message(
            "No active game in this channel.", ephemeral=True
        )
        return

    embed = discord.Embed(title="Game Status", color=Color.blue())

    # Basic game info
    embed.add_field(
        name="Game State",
        value=f"Started: {'Yes' if game.game_started else 'No'}\n"
        f"Description Phase: {'Yes' if game.description_phase_started else 'No'}\n"
        f"Current Round: {game.round_number}/{settings.rounds}",
        inline=False,
    )

    # Player list and voting status
    players = []
    for user_id in game.joined_users:
        user = await bot.fetch_user(user_id)
        missed = game.missed_rounds.get(user_id, 0)
        voted = "✅" if user_id in game.voted_users else "❌"
        players.append(f"{user.name} (Missed: {missed}) {voted}")

    embed.add_field(
        name=f"Players ({len(game.joined_users)}/{settings.max_players})",
        value="\n".join(players) if players else "No players yet",
        inline=False,
    )

    # Game duration if started
    if game.start_time:
        duration = datetime.now() - game.start_time
        embed.add_field(
            name="Duration",
            value=f"{duration.seconds // 60} minutes",
            inline=False,
        )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="leave", description="Leave the current game")
async def leave(interaction: Interaction):
    game = game_manager.get_game(interaction.channel.id)
    if not game:
        await interaction.response.send_message(
            "No active game found!", ephemeral=True
        )
        return

    if interaction.user.id not in game.joined_users:
        await interaction.response.send_message(
            "You're not in this game!", ephemeral=True
        )
        return

    if interaction.user.id in game.left_users:
        await interaction.response.send_message(
            "You've already left the game!", ephemeral=True
        )
        return

    # Remove player completely from game state
    game.remove_player(interaction.user.id)
    game.left_users.add(interaction.user.id)

    await interaction.response.send_message(
        f"{interaction.user.mention} has left the game."
    )

    # Check if game should end due to insufficient players
    settings = server_config.get_settings(str(interaction.guild_id))
    if len(game.joined_users) < settings.min_players:
        await interaction.channel.send(
            "Not enough players remaining. Game ending."
        )
        game_manager.end_game(interaction.channel.id)


@bot.tree.command(name="votekick", description="Start a vote to kick a player")
@commands.cooldown(1, 30, commands.BucketType.user)
async def votekick(interaction: Interaction, player: discord.Member):
    game = game_manager.get_game(interaction.channel.id)
    if not game or not game.game_started:
        await interaction.response.send_message(
            "No active game found!", ephemeral=True
        )
        return

    if player.id not in game.joined_users:
        await interaction.response.send_message(
            "That player is not in the game!", ephemeral=True
        )
        return

    if interaction.user.id not in game.joined_users:
        await interaction.response.send_message(
            "You're not in the game!", ephemeral=True
        )
        return

    view = VoteKickView(game, player.id)
    await interaction.response.send_message(
        f"Vote to kick {player.mention}? ({view.required_votes} votes needed)",
        view=view,
    )


@bot.tree.command(
    name="settings",
    description="Configure game settings for this server (Admin only)",
)
@commands.has_permissions(administrator=True)
@commands.cooldown(1, 5, commands.BucketType.guild)
async def settings(
    interaction: Interaction,
    min_players: Optional[int] = None,
    max_players: Optional[int] = None,
    rounds: Optional[int] = None,
    description_timeout: Optional[int] = None,
    vote_timeout: Optional[int] = None,
    multiple_imposters: Optional[bool] = None,
    imposter_ratio: Optional[float] = None,
):
    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used in a server.", ephemeral=True
        )
        return

    # Collect only provided settings
    update_settings = {}
    if min_players is not None:
        update_settings["min_players"] = min_players
    if max_players is not None:
        update_settings["max_players"] = max_players
    if rounds is not None:
        update_settings["rounds"] = rounds
    if description_timeout is not None:
        update_settings["description_timeout"] = description_timeout
    if vote_timeout is not None:
        update_settings["vote_timeout"] = vote_timeout
    if multiple_imposters is not None:
        update_settings["multiple_imposters"] = multiple_imposters
    if imposter_ratio is not None:
        # Convert percentage to decimal (e.g., 25 -> 0.25)
        update_settings["imposter_ratio"] = imposter_ratio / 100

    # Update settings
    success, message = server_config.update_server_settings(
        str(interaction.guild.id),
        **update_settings
    )

    if not success:
        await interaction.response.send_message(
            f"Failed to update settings: {message}", ephemeral=True
        )
        return

    # Get updated settings for display
    settings = server_config.get_settings(str(interaction.guild.id))

    embed = discord.Embed(
        title="Server Game Settings",
        color=Color.green(),
        description="Settings updated successfully!",
    )

    embed.add_field(name="Minimum Players", value=settings.min_players)
    embed.add_field(name="Maximum Players", value=settings.max_players)
    embed.add_field(name="Rounds", value=settings.rounds)
    embed.add_field(
        name="Description Timeout", value=f"{settings.description_timeout}s"
    )
    embed.add_field(name="Vote Timeout", value=f"{settings.vote_timeout}s")
    embed.add_field(
        name="Multiple Imposters", value=str(settings.multiple_imposters)
    )
    embed.add_field(
        name="Imposter Ratio", value=f"{settings.imposter_ratio * 100:.0f}%"
    )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="request", description="Request a new word to be added")
@commands.cooldown(1, 300, commands.BucketType.user)  # 5-minute cooldown
async def request_word(interaction: Interaction, word: str):
    success, message = await game_manager.word_manager.add_word(
        word, interaction.user.id
    )
    await interaction.response.send_message(message, ephemeral=True)


@bot.event
async def on_ready():
    print(f"{bot.user} is ready and online!")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


@bot.tree.error
async def on_command_error(interaction: Interaction, error: Exception):
    await ErrorHandler.handle_command_error(interaction, error)


def run_bot(token: str):
    """Start the bot with the provided token."""
    try:
        bot.run(token)
    except Exception as e:
        print(f"Failed to start bot: {e}")
        traceback.print_exception(type(e), e, e.__traceback__)
