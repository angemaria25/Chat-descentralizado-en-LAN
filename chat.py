import socket
import struct
import time
from uuid import uuid4 

def iniciar_sockets(puerto=9990):
    
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1) 
    udp_socket.bind(('0.0.0.0', puerto))
    
    tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_socket.bind(('0.0.0.0', puerto))
    tcp_socket.listen(5)
    
    print(f"[Red] Sockets UDP/TCP listos en puerto {puerto}")
    return udp_socket, tcp_socket

def generar_id_usuario():
    """Genera un ID único de 20 bytes para este cliente"""
    return uuid4().bytes[:20]

def enviar_echo(udp_socket, mi_id):
    """Envía un mensaje de descubrimiento a toda la red (Operación 0)"""
    header = struct.pack('!20s 20s B 59s',
                        mi_id,                  
                        b'\xff'*20,             
                        0,                     
                        b'\x00'*59)             
    
    udp_socket.sendto(header, ('255.255.255.255', 9990))
    print(f"[Descubrimiento] Enviando Echo con ID {mi_id.hex()}")
    
def manejar_echo(udp_socket, data, addr, mi_id, usuarios):
    id_origen = data[:20]
    
    if id_origen != mi_id:
        usuarios[id_origen] = (addr[0], time.time())
        print(f"[Descubrimiento] Usuario encontrado: {id_origen.hex()} desde {addr[0]}")
        
        respuesta = struct.pack('!B 20s 4s',
                                0,              
                                mi_id,          
                                b'\x00'*4)      
            
        udp_socket.sendto(respuesta, addr)
        
        
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