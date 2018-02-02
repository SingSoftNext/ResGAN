import os
import numpy as np
# For reproducibility 
np.random.seed(9999)

import sys

# theano imports 
import theano
import theano.tensor as T
from theano.tensor import fft

from keras import backend as K

# keras imports
from keras.models import Sequential, Model
from keras.layers import Dense, Reshape, Input
from keras.layers.merge import  add, concatenate, multiply
from keras.layers.core import Activation, Flatten, Dropout, Lambda, Masking
from keras.layers.recurrent import LSTM, GRU
from keras.layers.wrappers import TimeDistributed 
from keras.layers.noise import GaussianNoise
from keras.layers.advanced_activations import LeakyReLU
from keras.layers.normalization import BatchNormalization
from keras import regularizers
from keras.layers.convolutional import UpSampling1D, Convolution1D, MaxPooling1D, AveragePooling1D, ZeroPadding1D
from keras.optimizers import SGD, adam
import argparse
import math

# sklearn imports
from sklearn import preprocessing
from sklearn.externals import joblib

# netcdf for reading packaged data
from scipy.io import netcdf

import matplotlib as mpl
mpl.use('Agg') # no need for X-server
from matplotlib import pyplot as plt

# 'Globals'
fbins = 400
NFFT = (fbins-1)*2

#from pls_utils import *
from data_utils import *

# edge smoothing window
gen_filtwidths = np.asarray([15, 15, 15])
edgelen = sum(gen_filtwidths-1)
hannwin = np.hanning(edgelen)
smoothwin = np.concatenate((hannwin[:edgelen/2], np.ones(400-edgelen), hannwin[edgelen/2:]))

def theano_fft(x):

    x_win = x

    # zero-pad
    frame = T.zeros((x.shape[0], NFFT))
    frame = T.set_subtensor(frame[:,:x.shape[1]], x_win)
    
    # apply FFT
    x = fft.rfft(frame, norm='ortho')

    # get first half of spectrum
    x = x[:,:fbins] 
    # squared magnitude
    x = x[:,:, 0]**2 + x[:,:, 1]**2 

    # floor (prevents log from going to -Inf)
    x = T.maximum(x, 1e-9) # -90dB

    # map to log domain where 0dB -> 1 and -90dB -> -1
    x = (20.0/90.0)*T.log10(x) + 1.0

    # scale to weigh errors
    x = 0.1*x 

    return x 

# Keras wrapper for FFT layer
def fft_output_shape(x_shape):
    return (x_shape[0],fbins)

fft_layer = Lambda(theano_fft, output_shape=fft_output_shape)
fft_layer.trainable = False

# Keras wrapper for log 
def identity_output_shape(x_shape):
    return x_shape

def log_operation(x):
    return 10*T.log10(x)

log_layer = Lambda(log_operation, output_shape=identity_output_shape)
log_layer.trainable = False

def exp_operation(x):
    return T.pow(10.0, x/10.0)

exp_layer = Lambda(exp_operation, output_shape=identity_output_shape)
exp_layer.trainable = False

def win_operation(x):
    return win32_gpu*x

win_layer = Lambda(win_operation, output_shape=identity_output_shape)
win_layer.trainable = False

# fft model for transforming training set samples
def fft_model(model_name="fft_model"):
    x = Input(shape=(400,), name="fft_input")
    x_fft = fft_layer(x)
    model = Model(input=[x], output=[x_fft], name=model_name)
    return model

def time_glot_model(timesteps=128, input_dim=22, output_dim=400, model_name="time_glot_model"):

    ac_input = Input(shape=(timesteps, input_dim), name="ac_input")
 
    x_t = ac_input
    
    x_t = GRU(50, activation='relu', kernel_initializer='glorot_normal', 
              return_sequences=False, unroll=False)(x_t)
    
    x = x_t
    
    x = Dense(output_dim)(x)
    x = BatchNormalization()(x)
    x = Activation('relu')(x)

    x = Reshape((output_dim, 1))(x)    

    x = Convolution1D(filters=100,
                        kernel_size=15,
                        padding='same',
                        strides=1)(x)
    x = BatchNormalization()(x)
    x = LeakyReLU(0.1)(x)

    x = Convolution1D(filters=100,
                      kernel_size=15,
                      padding='same',
                      strides=1)(x)
    x = BatchNormalization()(x)
    x = LeakyReLU(0.1)(x)

    x = Convolution1D(filters=100,
                        kernel_size=15,
                        padding='same',
                        strides=1)(x)
    x = BatchNormalization()(x)
    x = LeakyReLU(0.1)(x)

    x = Convolution1D(filters=100,
                        kernel_size=15,
                        padding='same',
                        strides=1)(x)
    x = BatchNormalization()(x)
    x = LeakyReLU(0.1)(x)
                    
    x = Convolution1D(filters=1,
                      kernel_size=15,
                      padding='same',
                      strides=1)(x)

    # remove singleton outer dimension 
    x = Reshape((output_dim,))(x)

    x_t = x
        
    x_fft = fft_layer(x)

    model = Model(input=[ac_input], output=[x_t, x_fft], name=model_name)

    return model

def generator(input_dim=400, ac_dim=22, output_dim=400):
    
    pls_input = Input(shape=(input_dim,), name="pls_input")
    noise_input = Input(shape=(input_dim,), name="noise_input")

    pls = Reshape((input_dim, 1))(pls_input)    
    noise = Reshape((input_dim, 1))(noise_input)
 
    x = concatenate([pls, noise], axis=2) # concat as different channels

    x = Convolution1D(filters=100,
                        kernel_size=15,
                        padding='same',
                        strides=1)(x)
    x = BatchNormalization()(x)
    x = LeakyReLU(0.1)(x)

    x = concatenate([pls, x], axis=2) # concat as different channels

    x = Convolution1D(filters=100,
                      kernel_size=15,
                      padding='same',
                      strides=1)(x)
    x = BatchNormalization()(x)
    x = LeakyReLU(0.1)(x)

    x = concatenate([pls, x], axis=2) # concat as different channels

    x = Convolution1D(filters=100,
                        kernel_size=15,
                        padding='same',
                        strides=1)(x)
    x = BatchNormalization()(x)
    x = LeakyReLU(0.1)(x)

    x = concatenate([pls, x], axis=2) # concat as different channels
                    
    x = Convolution1D(filters=1,
                      kernel_size=15,
                      padding='same',
                        strides=1)(x)

    x = Activation('tanh')(x)

    # force additivity   
    x = add([pls, x]) 
             
    # remove singleton outer dimension 
    x = Reshape((output_dim,))(x)

    # add fft channel to output
    x_fft = fft_layer(x)
     
    model = Model(inputs=[pls_input, noise_input], outputs=[x, x_fft],
                  name="generator")

    return model

def discriminator(input_dim=400):

    pls_input = Input(shape=(input_dim,), name="pls_input") 
    fft_input = Input(shape=(input_dim,), name="fft_input") 

    x = Reshape((input_dim, 1))(pls_input)
    x_fft = Reshape((input_dim, 1))(fft_input)    

    x = concatenate([x, x_fft], axis=2) # concat as different channels
    
    # input shape batch_size x 1 (number of channels) x 400 (length of pulse)
    x = Convolution1D(filters=64,
                        kernel_size=7,
                        strides=3)(x)
    x = BatchNormalization()(x)
    x = LeakyReLU(0.1)(x)

    # shape [batch_size x 64 x 132]
    x = Convolution1D(filters=128,
                        kernel_size=7,
                        strides=3)(x)
    x = BatchNormalization()(x)
    x = LeakyReLU(0.1)(x)

    # shape [batch_size x 128 x 42]
    x = Convolution1D(filters=256,
                        kernel_size=7,
                        strides=3)(x)
    x = BatchNormalization()(x)
    x = LeakyReLU(0.1)(x)

    peek_output = x # used for generator training regularization

    # shape [batch_size x 256 x 12]
    x = Convolution1D(filters=128,
                        kernel_size=5,
                        strides=2)(x)
    x = BatchNormalization()(x)
    x = LeakyReLU(0.1)(x)

    # shape [batch_size x 128 x 4]                                                             
 
    #nn.Sigmoid() # use for normal gan, comment out for LS-GAN                                 
    x = Convolution1D(filters=1,
                      kernel_size=3,
                      strides=2)(x)

    # shape [batch_size x 1 x 1] 
    x = Reshape((1,))(x)

    model = Model(inputs=[pls_input, fft_input], outputs=[x, peek_output],
                  name="discriminator")

    return model

def gan_container(generator, discriminator, input_dim=400, ac_dim=22):
   
    discriminator.trainable = False

    pls_input = Input(shape=(input_dim,), name="pls_input")
    noise_input = Input(shape=(input_dim,), name="noise_input")

    x, x_fft = generator([pls_input, noise_input])
    x, peek_output = discriminator([x, x_fft])

    model = Model(inputs=[pls_input, noise_input], outputs=[x, peek_output],
                  name="gan_container")
    return model


def plot_feats(generated_feats, epoch, index, ext='', fig_dir="./figures", fig_type=""):
    plt.figure()
    for row in generated_feats:
        plt.plot(row)
    plt.savefig(fig_dir + '/' + fig_type +'_epoch{}_index{}'.format(epoch, index) + ext + '.png')
    plt.close()

def train_pls_model(BATCH_SIZE, data_dir, file_list, context_len=32, max_files=30):
    
    timesteps = context_len

    optim = adam(lr=0.0001)
    pls_model = time_glot_model(timesteps=timesteps)
    pls_model.compile(loss=['mse', 'mse'], loss_weights=[1.0, 0.0], optimizer=optim) # disregard fft loss

    fft_mod = fft_model()

    # train glot model in time domain first 
    no_epochs = 30
    max_epochs_no_improvement = 5

    patience = max_epochs_no_improvement
    best_val_loss = 1e20
    for epoch in range(no_epochs):
        print("Pre-train epoch is", epoch)
        epoch_error = [0.0, 0.0]
        total_batches = 0
        val_data = []
        for data in nc_data_provider(file_list, data_dir,
                                     max_files=max_files, context_len=timesteps):

            if len(val_data) == 0:
                val_data = data
                continue

            X_train = data[0]
            Y_train = data[1]

            no_batches = int(X_train.shape[0] / BATCH_SIZE)                
            print("Number of batches", int(X_train.shape[0] / BATCH_SIZE))

            # shuffle data
            ind = np.random.permutation(X_train.shape[0])
            X_train = X_train[ind]
            Y_train = Y_train[ind]
            for index in range(int(X_train.shape[0] / BATCH_SIZE)):
                x_feats_batch = X_train[
                    index * BATCH_SIZE:(index + 1) * BATCH_SIZE]
                y_feats_batch = Y_train[
                    index * BATCH_SIZE:(index + 1) * BATCH_SIZE]

                x_feats_batch_fft = fft_mod.predict(x_feats_batch)
                
                d = pls_model.train_on_batch([y_feats_batch],
                                             [x_feats_batch, x_feats_batch_fft])
                    
                epoch_error += d

                if  (index + total_batches) % 500 == 0:

                    print("pre-training batch %d, wave loss: %f, spec loss %f" %
                          (index+total_batches, d[0], d[1]))

                    wave, spec = pls_model.predict([y_feats_batch])

                    wav_gen = wave[0,:]
                    wav_ref = x_feats_batch[0,:]
                    wavs = np.array([wav_ref, wav_gen])
                    plot_feats(wavs, epoch, index+total_batches, ext='.wave-pls')

                    spec_gen = spec[0,:]
                    spec_ref = x_feats_batch_fft[0,:]
                    specs = np.array([spec_ref, spec_gen])
                    plot_feats(specs, epoch, index+total_batches, ext='.spec-pls')
                    
            total_batches += no_batches

        epoch_error[0] /= total_batches
        epoch_error[1] /= total_batches

        val_spec = fft_mod.predict(val_data[0])
        val_loss = pls_model.evaluate([val_data[1]],
                                      [val_data[0], val_spec],
                                      batch_size=BATCH_SIZE)
        
        print("epoch %d validation wave loss: %f ,spec loss %f \n" %
              (epoch, val_loss[0], val_loss[1]))

        print("epoch %d training wave loss: %f, spec loss %f \n" %
              (epoch, epoch_error[0], epoch_error[1]))
        
        # only on wave loss
        if val_loss[0] < best_val_loss:
            best_val_loss = val_loss[0]
            patience = max_epochs_no_improvement
            pls_model.save_weights('./pls.model')
        else:
            patience -= 1

        if patience == 0:
            break

    print "Finished training" 


def train_noise_model(BATCH_SIZE, data_dir, file_list, save_weights=False,
                      context_len=32, max_files=30, stats=None):
    
    timesteps = context_len

    optim_container = adam(lr=1e-4)
    optim_discriminator = SGD(lr=1e-5)

    fft_mod = fft_model()
    pls_model = time_glot_model(timesteps=timesteps)

    pls_model.compile(loss=['mse','mse'], loss_weights=[1.0, 1.0], optimizer='adam')
    pls_model.load_weights("./pls.model")

    disc_model = discriminator()
    gen_model = generator()
    disc_on_gen = gan_container(gen_model, disc_model)
 
    gen_model.compile(loss='mse', optimizer="adam")

    # use peek adversarial and peek mse loss for training generator
    disc_model.trainable = False
    disc_on_gen.compile(loss=['mse','mse'], loss_weights=[1.0, 1.0], optimizer=optim_container) 

    # don't use peek loss for discriminator
    disc_model.trainable = True
    disc_model.compile(loss=['mse','mse'], loss_weights=[1.0, 0.0], optimizer=optim_discriminator) 

    print "Discriminator model:"
    print disc_model.summary()
    print "Generator model:"
    print gen_model.summary()
    print "Joint model:"
    print disc_on_gen.summary()

    label_fake = np.zeros((BATCH_SIZE, 1), dtype=np.float32)
    label_real = np.ones((BATCH_SIZE, 1), dtype=np.float32)

    # train residual GAN with FFT     
    no_epochs = 20
    for epoch in range(no_epochs):
        print("Epoch is", epoch)

        epoch_error = 0
        total_batches = 0
      
        for data in nc_data_provider(file_list, data_dir,
                                     max_files=max_files, context_len=timesteps):   

            X_train = data[0]
            Y_train = data[1]

            pls_len = X_train.shape[1]

            no_batches = int(X_train.shape[0] / BATCH_SIZE)    

            # shuffle data
            ind = np.random.permutation(X_train.shape[0])
            X_train = X_train[ind]
            Y_train = Y_train[ind]
            for index in range(int(X_train.shape[0] / BATCH_SIZE)):
                x_feats_batch = X_train[
                    index * BATCH_SIZE:(index + 1) * BATCH_SIZE]
                y_feats_batch = Y_train[
                    index * BATCH_SIZE:(index + 1) * BATCH_SIZE]

                x_pred_batch, x_pred_batch_fft = pls_model.predict([y_feats_batch])
                                
                pls_pred = x_pred_batch
                pls_real = x_feats_batch

                # smoothing windows to prevent edge effects
                pls_pred *= smoothwin
                pls_real *= smoothwin

                # evaluate target fft
                fft_real = fft_mod.predict(pls_real)

                noise = np.random.randn(BATCH_SIZE, pls_len)

                # train generator through discriminator
                _, peek_real = disc_model.predict([pls_real, fft_real])
                disc_model.trainable = False
                loss_g = disc_on_gen.train_on_batch([pls_pred, noise], [label_real, peek_real])
 
                noise = np.random.randn(BATCH_SIZE, pls_len)

                # train discriminator with real data
                disc_model.trainable = True
                loss_dr = disc_model.train_on_batch([pls_real, fft_real], [label_real, peek_real])

                # train discriminator with fake data
                pls_fake, fft_fake = gen_model.predict([pls_pred, noise])
                loss_df = disc_model.train_on_batch([pls_fake, fft_fake], [label_fake, peek_real])
        
                if (index + total_batches) % 500 == 0:

                    print("training batch %d, G loss: %f, D loss (real): %f, D loss (fake): %f" %
                          (index + total_batches, loss_g[0], loss_dr[0], loss_df[0]))

                if (index + total_batches) % 500 == 0:

                    wav_ref = pls_real[0,:]
                    wav_gen = pls_pred[0,:]
                    wav_noised = pls_fake[0,:]
                    wavs = np.array([wav_ref, wav_gen, wav_noised])
                    plot_feats(wavs, epoch, index+total_batches, ext='.wave')
                 
            total_batches += no_batches

        gen_model.save_weights('./models/noise_gen_epoch' + str(epoch) + '.model')

    print "Finished noise model training" 

def generate(file_list, data_dir, output_dir, context_len=32, stats=None,
             base_model_path='./pls.model', gan_model_path='./noise_gen.model'):
    
    pulse_model = time_glot_model(timesteps=context_len)
    gan_model = generator()
    
    pulse_model.compile(loss='mse', optimizer="adam")
    gan_model.compile(loss='mse', optimizer="adam")

    pulse_model.load_weights(base_model_path)
    gan_model.load_weights(gan_model_path)

    for data in nc_data_provider(file_list, data_dir, input_only=True, 
                                 context_len=context_len):
        for fname, ac_data in data.iteritems():
            print fname
                                              
            pls_pred, _ = pulse_model.predict([ac_data])
            noise = np.random.randn(pls_pred.shape[0], pls_pred.shape[1])
            pls_gan, _ = gan_model.predict([pls_pred, noise])
            
            out_file = os.path.join(args.output_dir, fname + '.pls')
            pls_gan.astype(np.float32).tofile(out_file)

            out_file = os.path.join(args.output_dir, fname + '.pls_nonoise')
            pls_pred.astype(np.float32).tofile(out_file)
    
def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str)
    parser.add_argument("--batch_size", type=int, default=128)
    parser.add_argument("--nice", dest="nice", action="store_true")
    parser.add_argument("--data_dir", type=str,
                        default="./traindata")
    parser.add_argument("--testdata_dir", type=str,
                        default="./testdata")
    parser.add_argument("--output_dir", type=str,
                        default="./output")
    parser.add_argument("--rnn_context_len", type=int, default=64)
    parser.add_argument("--max_files", type=int, default=100)
    parser.set_defaults(nice=False)
    parser.add_argument("--gan_model", type=str,
                        default=None)
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    args = get_args()

    if args.mode == "train":

        file_list = os.listdir(args.data_dir)

        train_pls_model(BATCH_SIZE=args.batch_size, data_dir=args.data_dir,
                        file_list=file_list, max_files=args.max_files,
                        context_len=args.rnn_context_len)

        stats = norm_stats(file_list[0], args.data_dir)

        train_noise_model(BATCH_SIZE=args.batch_size, data_dir=args.data_dir,
                          file_list=file_list, max_files=args.max_files,
                          context_len=args.rnn_context_len,
                          stats=stats)


    elif args.mode == "train_pls_model":
        print "MODE: Training time domain pulse  model"
    
        file_list = os.listdir(args.data_dir)

        train_pls_model(BATCH_SIZE=args.batch_size, data_dir=args.data_dir,
                        file_list=file_list, max_files=args.max_files,
                        context_len=args.rnn_context_len)
        
    elif args.mode == "train_noise_model":
        print "MODE: Training noise model"
    
        file_list = os.listdir(args.data_dir)

        stats = norm_stats(file_list[0], args.data_dir)

        train_noise_model(BATCH_SIZE=args.batch_size, data_dir=args.data_dir,
                          file_list=file_list, max_files=args.max_files,
                          context_len=args.rnn_context_len,
                          stats=stats)

    elif args.mode == "generate":
 
        test_dir = args.testdata_dir
        file_list = os.listdir(test_dir)

        stats = norm_stats(file_list[0], test_dir)

        generate(data_dir=test_dir, file_list=file_list,
                 output_dir=args.output_dir,
                 context_len=args.rnn_context_len, stats=stats,
                 gan_model_path=args.gan_model)

        
