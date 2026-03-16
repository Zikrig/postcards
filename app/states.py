# FSM state groups for the bot
from aiogram.fsm.state import State, StatesGroup


class AuthStates(StatesGroup):
    waiting_password = State()


class AdminStates(StatesGroup):
    waiting_prompt_title = State()
    waiting_prompt_template = State()
    waiting_prompt_edit_title = State()
    waiting_prompt_edit_template = State()
    waiting_prompt_edit_variable_name = State()
    waiting_prompt_edit_variable_description = State()
    waiting_prompt_edit_variable_options = State()
    waiting_variable_description = State()
    waiting_text_options = State()
    waiting_text_allow_custom = State()
    waiting_prompt_reference = State()
    waiting_gen_title = State()
    waiting_gen_idea = State()
    waiting_feach_add_option = State()
    waiting_import_json = State()
    waiting_prompt_examples = State()
    waiting_promo_code = State()
    waiting_promo_credits = State()
    waiting_promo_max_uses = State()
    waiting_tag_name = State()
    waiting_tag_edit_name = State()


class GenerateStates(StatesGroup):
    waiting_variable = State()
