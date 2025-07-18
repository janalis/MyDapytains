from dotenv import dotenv_values
from logging import info
from os import path

root_dir = path.abspath(path.dirname(path.dirname(__file__)))

class Config:
    __env = {}

    def __init__(self):
        self.load_env('.env')
        self.load_env(f'.env.{self.get('SERVER_ENV')}')

    def load_env(self, name, local = True):
        if path.isfile(path.join(root_dir, name)):
            info(f'Loading configuration from {name}')
            self.__env = {
                **self.__env,
                **dotenv_values(str(path.join(root_dir, name))),
            }
            if local:
                self.load_env(f'{name}.local', False)

    def get(self, name):
        return self.__env[name].replace('{root_dir}', root_dir)
