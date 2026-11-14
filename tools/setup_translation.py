"""Install the official Argos en->ru model once; never send any messages."""
import sys


def main():
    if sys.platform == 'win32':
        import truststore
        truststore.inject_into_ssl()
    try:
        import argostranslate.package as packages
    except ImportError:
        raise SystemExit('Установи зависимости: .\\hub --translation install --python 3.13')
    if any(p.from_code == 'en' and p.to_code == 'ru' for p in packages.get_installed_packages()):
        print('Модель EN -> RU уже установлена.', flush=True)
        return
    print('Загружаем официальный каталог моделей Argos…', flush=True)
    packages.update_package_index()
    model = next((p for p in packages.get_available_packages()
                  if p.from_code == 'en' and p.to_code == 'ru'), None)
    if model is None:
        raise SystemExit('Модель en-ru отсутствует в каталоге Argos. Установка не выполнена.')
    print('Скачиваем и устанавливаем модель EN -> RU…', flush=True)
    packages.install_from_path(model.download())
    print('Локальный перевод EN -> RU готов.', flush=True)


if __name__ == '__main__':
    main()
