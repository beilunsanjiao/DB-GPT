import logging
from pathlib import Path
from typing import Dict, Type

from dbgpt import SystemApp
from dbgpt.agent.util.api_call import ApiCall
from dbgpt.configs.model_config import ROOT_PATH
from dbgpt.util.tracer import root_tracer, trace
from dbgpt_app.scene import BaseChat, ChatScene
from dbgpt_app.scene.base_chat import ChatParam
from dbgpt_app.scene.chat_db.auto_execute.config import ChatWithDBExecuteConfig
from dbgpt_app.scene.chat_db.safe_sql import ReadOnlySqlGuard, SafeSqlExecutor
from dbgpt_app.scene.chat_db.semantic_catalog import SemanticCatalog
from dbgpt_serve.core.config import GPTsAppCommonConfig
from dbgpt_serve.datasource.manages import ConnectorManager

logger = logging.getLogger(__name__)


def _resolve_catalog_path(catalog_path: str | None) -> Path:
    if not catalog_path:
        raise ValueError(
            "chat_with_db_execute requires app.configs.catalog_path for governed SQL"
        )
    path = Path(catalog_path).expanduser()
    if not path.is_absolute():
        path = Path(ROOT_PATH) / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError("Semantic catalog file does not exist")
    return path


class ChatWithDbAutoExecute(BaseChat):
    chat_scene: str = ChatScene.ChatWithDbExecute.value()

    """Number of results to return from the query"""

    @classmethod
    def param_class(cls) -> Type[GPTsAppCommonConfig]:
        return ChatWithDBExecuteConfig

    def __init__(self, chat_param: ChatParam, system_app: SystemApp):
        """Chat Data Module Initialization
        Args:
           - chat_param: Dict
            - chat_session_id: (str) chat session_id
            - current_user_input: (str) current user input
            - model_name:(str) llm model name
            - select_param:(str) dbname
        """
        self.db_name = chat_param.select_param
        self.curr_config = chat_param.real_app_config(ChatWithDBExecuteConfig)
        super().__init__(chat_param=chat_param, system_app=system_app)
        if not self.db_name:
            raise ValueError(
                f"{ChatScene.ChatWithDbExecute.value} mode should chose db!"
            )
        with root_tracer.start_span(
            "ChatWithDbAutoExecute.get_connect", metadata={"db_name": self.db_name}
        ):
            local_db_manager = ConnectorManager.get_instance(self.system_app)
            self.database = local_db_manager.get_connector(self.db_name)
        if self.curr_config.max_num_results <= 0:
            raise ValueError("max_num_results must be positive")
        dialect = (self.database.dialect or "").strip().casefold()
        if dialect != "sqlite":
            raise ValueError(
                "Governed chat_with_db_execute currently supports SQLite only; "
                f"received dialect {dialect or '<empty>'}"
            )
        catalog_path = _resolve_catalog_path(self.curr_config.catalog_path)
        self._catalog = SemanticCatalog.load(catalog_path)
        self._sql_executor = SafeSqlExecutor(
            ReadOnlySqlGuard(self._catalog),
            self.database.run_to_df,
            dialect,
            self.curr_config.max_num_results,
        )
        self.api_call = ApiCall()

    @trace()
    async def generate_input_values(self) -> Dict:
        """Generate model inputs from the same catalog used for authorization."""

        input_values = {
            "db_name": self.db_name,
            "user_input": self.current_user_input.last_text,
            "top_k": self.curr_config.max_num_results,
            "dialect": self.database.dialect,
            "table_info": self._catalog.render_prompt_context(),
            "display_type": self._generate_numbered_list(),
        }
        return input_values

    def do_action(self, prompt_response):
        if prompt_response.sql:
            return self._sql_executor.execute(prompt_response.sql)
        return None
