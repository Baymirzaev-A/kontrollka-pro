from .base_script import BaseScript
import re
import time
import logging

logger = logging.getLogger(__name__)


class DeleteTacacsScript(BaseScript):

    def get_name(self):
        return "Удаление TACACS"

    def get_description(self):
        return "Удаляет конфигурацию TACACS сервера с устройства"

    def pre_check(self, connection, device_info):
        """Проверяет, используется ли TACACS"""
        try:
            command = "display hwtacacs-server template tacacsgroup verbose"
            logger.info(f"Pre-check: {command}")

            output = connection.send_command(
                command,
                expect_string=r'[>#]',
                delay_factor=2,
                max_loops=500
            )

            # Ищем счётчики пакетов
            patterns = [
                r"request\s+packet\s+number:\s*(\d+)",
                r"response\s+packet\s+number:\s*(\d+)"
            ]

            request_count = None
            response_count = None

            for pattern in patterns:
                if "request" in pattern.lower():
                    match = re.search(pattern, output, re.IGNORECASE)
                    if match and request_count is None:
                        request_count = int(match.group(1))
                elif "response" in pattern.lower():
                    match = re.search(pattern, output, re.IGNORECASE)
                    if match and response_count is None:
                        response_count = int(match.group(1))

            if request_count is not None and response_count is not None:
                if request_count == 0 and response_count == 0:
                    return True, "TACACS не используется, можно удалять"
                else:
                    return False, f"TACACS используется (request: {request_count}, response: {response_count})"
            else:
                # Если нет счётчиков - считаем что можно удалять
                return True, "Счётчики не найдены, возможно TACACS не настроен"

        except Exception as e:
            logger.error(f"Ошибка в pre_check: {str(e)}")
            return False, f"Ошибка проверки: {str(e)}"

    def execute(self, connection, device_info):
        """Удаляет конфигурацию TACACS"""
        logger.info(f"Удаление TACACS на {device_info['name']}")

        # Проверяем текущий промпт
        current_prompt = connection.find_prompt()
        logger.info(f"Текущий промпт: {current_prompt}")

        # Команды для удаления
        commands = [
            "system-view",
            "undo hwtacacs-server template tacacsgroup",
            "return",
            "save",
            "Y"
        ]

        results = []

        for cmd in commands:
            logger.info(f"Команда: {cmd}")

            try:
                if cmd == "save":
                    # Для save нужна особая обработка
                    output = connection.send_command_timing(cmd, strip_command=False)
                    time.sleep(1)
                    if re.search(r'\[Y/N\]', output, re.IGNORECASE):
                        output += connection.send_command_timing('Y', strip_command=False)
                else:
                    output = connection.send_command_timing(cmd, strip_command=False)

                results.append(f"> {cmd}\n{output}")
                time.sleep(0.5)

            except Exception as e:
                logger.error(f"Ошибка команды {cmd}: {str(e)}")
                results.append(f"> {cmd}\nОШИБКА: {str(e)}")

        return "\n".join(results)

    def post_check(self, connection, device_info):
        """Проверяет что TACACS удалён"""
        try:
            output = connection.send_command(
                "display hwtacacs-server-template tacacsgroup verbose",
                expect_string=r'[>#]',
                delay_factor=2
            )

            if "doesn't exist" in output or "not found" in output.lower():
                return True, "TACACS успешно удалён"
            else:
                return False, "TACACS шаблон всё ещё существует"

        except Exception as e:
            return False, f"Ошибка проверки: {str(e)}"