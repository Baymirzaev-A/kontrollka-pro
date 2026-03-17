import os
import importlib
import inspect
import logging
from .base_script import BaseScript

logger = logging.getLogger(__name__)

# Кэш для скриптов
_scripts_cache = None


def discover_scripts():
    """Находит все скрипты в папке scripts"""
    global _scripts_cache

    if _scripts_cache is not None:
        return _scripts_cache

    scripts = []
    scripts_dir = os.path.dirname(__file__)
    logger.info(f"🔍 Поиск скриптов в папке: {scripts_dir}")

    # Логируем все файлы в папке для отладки
    all_files = os.listdir(scripts_dir)
    logger.info(f"📄 Все файлы в папке: {all_files}")

    for filename in all_files:
        if filename.endswith('.py') and filename != '__init__.py' and filename != 'base_script.py':
            module_name = filename[:-3]
            logger.info(f"📄 Найден файл: {filename}")
            try:
                module = importlib.import_module(f'.{module_name}', package='scripts')

                # Ищем все классы, наследующие BaseScript
                found_classes = False
                for name, obj in inspect.getmembers(module):
                    if (inspect.isclass(obj) and
                            issubclass(obj, BaseScript) and
                            obj != BaseScript):
                        found_classes = True
                        script_instance = obj()
                        scripts.append({
                            'id': f"{module_name}.{name}",
                            'name': script_instance.get_name(),
                            'description': script_instance.get_description(),
                            'class': obj,
                            'instance': script_instance
                        })
                        logger.info(f"✅ Загружен скрипт: {script_instance.get_name()}")

                if not found_classes:
                    logger.warning(f"⚠️ В файле {filename} нет классов, наследующих BaseScript")

            except Exception as e:
                logger.error(f"❌ Ошибка загрузки {module_name}: {e}")
                import traceback
                logger.error(traceback.format_exc())

    logger.info(f"📊 Всего загружено скриптов: {len(scripts)}")
    _scripts_cache = None
    return scripts


def get_script(script_id):
    """Возвращает экземпляр скрипта по ID"""
    for script in discover_scripts():
        if script['id'] == script_id:
            return script['class']()
    return None


def get_all_scripts():
    """Возвращает список всех скриптов"""
    return discover_scripts()