import os
import time
import socket
import struct
from uuid import uuid4 

PUERTO = 9990
BROADCAST_ADDR = '255.255.255.255'

MI_ID = os.urandom(20)  
usuarios_conectados = {}  

def iniciar_sockets():
    """Configura y retorna los sockets UDP y TCP"""
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1) 
        udp_socket.bind(('0.0.0.0', puerto))
        
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_socket.bind(('0.0.0.0', puerto))
        tcp_socket.listen(5)
        
        print(f"[Red] Sockets UDP/TCP listos en puerto {puerto}")
        return udp_socket, tcp_socket
    
    except Exception as e:
        print(f"[Error] No se pudo iniciar sockets: {e}")
        return None, None

def enviar_echo(udp_socket):
    """Envía mensaje de descubrimiento a toda la red"""
    header = struct.pack('!20s 20s B 59s',
                        MI_ID,                  
                        b'\xff'*20,             
                        0,                     
                        b'\x00'*59)             
    
    udp_socket.sendto(header, (BROADCAST_ADDR, PUERTO))
    print(f"[Descubrimiento] Echo enviado con ID: {MI_ID.hex()}")
    
def manejar_echo(data, addr, udp_socket):
    """Procesa mensajes de descubrimiento recibidos"""
    try:
        id_origen = data[:20]
        
        if id_origen == MI_ID:
            return
        
        usuarios_conectados[id_origen] = (addr[0], time.time())
        print(f"[Descubrimiento] Usuario encontrado: {id_origen.hex()} desde {addr[0]}")
        
        respuesta = struct.pack('!B 20s 4s',
                                0,              
                                MI_ID,          
                                b'\x00'*4)      
            
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