from eva_agente.nucleo import Interface


class Registro(Interface):
    def __init__(self):
        self.estados, self.banners, self.avisos, self.revogados = [], [], [], []

    def estado(self, estado, texto):
        self.estados.append((estado, texto))

    def banner(self, visivel, texto=""):
        self.banners.append((visivel, texto))

    def aviso(self, titulo, texto):
        self.avisos.append((titulo, texto))

    def token_invalido(self, motivo):
        self.revogados.append(motivo)
