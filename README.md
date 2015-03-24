Merged Mining Fork (multiple-chain) by nonce-pool

#Description
Stratum-mining is a pooled mining protocol. It is a replacement for *getwork* based pooling servers by allowing clients to generate work. The stratum protocol is described [here](http://mining.bitcoin.cz/stratum-mining) in full detail.

This is a implementation of stratum-mining for scrypt based coins. It is compatible with *MPOS* as it complies with the standards of *pushpool*. The end goal is to build on these standards to come up with a more stable solution.

The goal is to make a reliable stratum mining server for a wide range of coins unlike other forks where the code is limited to specific algorithm's. Over time I will develop this to be more feature rich and very stable. If you would like to see a feature please file a feature request. 

**NOTE:** This fork is still in development. Many features may be broken. Please report any broken features or issues.

#Requirements
*stratum-mining* is built in python. I have been testing it with 2.7.3, but it should work with other versions. The requirements for running the software are below.
* Python 2.7+
* python-twisted
* stratum
* MySQL Server 

#Installation

The installation of this *stratum-mining* can be found in the Repo Wiki. 

#Credits

* Original version by Slush0 and ArtForz (original stratum code)
* More Features added by GeneralFault, Wadee Womersley, Viperaus, TheSeven and Moopless
* Multi Algo, Vardiff, DB and MPOS support done by Ahmed_Bodi, penner42 and Obigal
* Merged-Mining added by nonce-pool

#License
This software is provides AS-IS without any warranties of any kind. Please use at your own risk. 

