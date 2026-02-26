from socket import *

serverPort = 12000

#create TCP socket
serverSocket = socket(AF_INET, SOCK_STREAM)

#bind socket to local port number 12000
serverSocket.bind(('', serverPort))

#server begins listening for  incoming TCP requests
serverSocket.listen(1)

print("The server is ready to receive")
while True:
    #server waits on accept() for incoming requests, new socket created on return
    connectionSocket, clientAddress = serverSocket.accept()
    
    #read bytes from socket (but not address as in UDP)
    sentence = connectionSocket.recv(1024).decode()
    print("""
          Received message from client:
          """, sentence)
    
    #process the message
    capitalizedSentence = sentence.upper()
    connectionSocket.send(capitalizedSentence.encode())
    
    #close connection to this client
    connectionSocket.close()