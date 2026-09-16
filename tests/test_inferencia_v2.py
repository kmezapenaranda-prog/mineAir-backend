from src.data.generador import ConfiguracionGenerador, generar_datos_sinteticos
from src.model.inferencia import MotorInferencia, nivel_desde_probabilidad


def test_motor_sin_artefactos_no_afirma_estar_listo(tmp_path):
    motor = MotorInferencia(tmp_path)
    assert not motor.listo and motor.predecir(generar_datos_sinteticos(ConfiguracionGenerador(dias=1))) == []


def test_nivel_desde_probabilidad_tres_bandas():
    assert nivel_desde_probabilidad(0.10, umbral_evacuar=0.50) == "normal"
    assert nivel_desde_probabilidad(0.35, umbral_evacuar=0.50) == "atencion"  # 70% del umbral
    assert nivel_desde_probabilidad(0.50, umbral_evacuar=0.50) == "evacuar"
