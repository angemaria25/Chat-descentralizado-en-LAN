import socket
import threading

PORT = 9990
BROADCAST_IP = '255.255.255.255'
MY_USER_ID = "User01".ljust(20, '\x00').encode('utf-8')

def crear_socket_udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(('', PORT))  
    return s

def enviar_echo(sock):
    mensaje = bytearray(100)
    mensaje[0:20] = MY_USER_ID
    mensaje[20:40] = b'\xFF' * 20  
    mensaje[40] = 0  
    sock.sendto(mensaje, (BROADCAST_IP, PORT))
    print("[Echo enviado]")
    

def escuchar(sock):
    while True:
        data, addr = sock.recvfrom(1024)
        if len(data) == 100 and data[40] == 0:  
            print(f"[Echo recibido] de {addr}")
            respuesta = bytearray(25)
            respuesta[0] = 0  
            respuesta[1:21] = MY_USER_ID
            sock.sendto(respuesta, addr)
            print(f"[Respondí a {addr}]")
        elif len(data) == 25 and data[0] == 0:  
            user_id = data[1:21].decode('utf-8').strip('\x00')
            print(f"[Usuario encontrado] {user_id} en {addr}")

def main():
    sock = crear_socket_udp()
    threading.Thread(target=escuchar, args=(sock,), daemon=True).start()
    enviar_echo(sock)
    
    while True:
        pass
    
if __name__ == "__main__":
    main()