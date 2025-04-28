import os
import time
import socket
import struct
import threading 
from queue import Queue 

PUERTO = 9990
BROADCAST_ADDR = '255.255.255.255'
TIMEOUT = 10

mi_id = os.urandom(20)  
usuarios_conectados = {}  
mensajes_recibidos = Queue()
archivos_recibidos = Queue()
tcp_server_running = True

udp_socket = None
tcp_socket = None

def iniciar_sockets():
    """Configura y retorna los sockets UDP y TCP"""
    
    global udp_socket, tcp_socket
    
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1) 
        udp_socket.bind(('0.0.0.0', PUERTO))
        
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_socket.bind(('0.0.0.0', PUERTO))
        tcp_socket.listen(5)
        
        print(f"[Red] Sockets iniciados en puerto {PUERTO}")
        return udp_socket, tcp_socket
    
    except Exception as e:
        print(f"[Error] No se pudieron iniciar los sockets: {e}")
        raise

###########################################
#Operación 0: Echo-Reply (Descubrimiento).
###########################################
def enviar_echo(udp_socket):
    """Envía mensaje de descubrimiento a toda la red (broadcast)"""
    
    try:
        header = struct.pack('!20s 20s B B 8S 50s',
                            mi_id,     
                            b'\xff'*20,
                            0,             
                            0,     
                            b'\x00'*8,                
                            b'\x00'*50)             
        
        udp_socket.sendto(header, (BROADCAST_ADDR, PUERTO))
        print(f"[Descubrimiento] Echo enviado.")
        
    except Exception as e:
        print(f"[Error] Al enviar echo: {e}")
    
def manejar_echo(data, addr):
    """Procesa mensajes de descubrimiento(echo) recibidos"""
    
    try:
        id_origen = data[:20]
        
        if id_origen == mi_id:
            return
        
        usuarios_conectados[id_origen] = (addr[0], time.time())
        print(f"[Descubrimiento] Usuario encontrado: {id_origen.hex()} desde {addr[0]}")
        
        #Preparar respuesta (Reply)
        respuesta = struct.pack('!B 20s 4s',
                                0,              
                                mi_id,          
                                b'\x00'*4)     
        
        #Enviar respuesta al remitente original
        udp_socket.sendto(respuesta, addr)
            
    except Exception as e:
        print(f"[Error] Al procesar Echo: {e}")
        
if __name__ == "__main__":
    try:
        udp_socket, tcp_socket = iniciar_sockets()
        mi_id = generar_id_usuario()
        usuarios_conectados = {}  
        
        print(f"\nTu ID de usuario es: {mi_id.hex()}")
        print("Iniciando descubrimiento...\n")
        
        enviar_echo(udp_socket, mi_id)

        while True:
            try:
                data, addr = udp_socket.recvfrom(1024)
                
                if len(data) > 40:
                    op_code = data[40]
                    
                    if op_code == 0:
                        manejar_echo(udp_socket, data, addr, mi_id, usuarios_conectados)
            
            except KeyboardInterrupt:
                raise
            except:
                print("[Error] Paquete malformado")
                
            if time.time() % 5 < 0.1: 
                enviar_echo(udp_socket, mi_id)

    except KeyboardInterrupt:
        print("\nCerrando aplicación...")
        udp_socket.close()
        tcp_socket.close()